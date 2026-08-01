from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.services.walmart_exact_price_enrichment import (
    _apply_exact_payload_offer_identity,
    _candidate_item_id,
    _merge_exact_candidate,
    _percent_off,
    _positive_number,
    _trusted_reference,
    exact_detail_verified_candidates,
)
from sniperplug.services.walmart_global_offer_memory import (
    ensure_global_offer_memory_table,
    exact_offer_identity,
    maybe_prune_global_offer_memory,
    observe_exact_offer,
)


QUEUE_TABLE = "walmart_exact_detail_queue"
QUEUE_MAX_ROWS = 50_000
QUEUE_RETENTION_DAYS = 14
QUEUE_VERIFIED_SNAPSHOT_MAX_AGE_MINUTES = 90
QUEUE_LEASE_SECONDS = 120
QUEUE_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
QUEUE_RETRY_BASE_SECONDS = 5 * 60
QUEUE_RETRY_MAX_SECONDS = 6 * 60 * 60

_last_cleanup_monotonic = 0.0


@dataclass(frozen=True)
class VerificationQueueEnqueueResult:
    discovered: int = 0
    queued_unique: int = 0
    pending_total: int = 0

    def summary_line(self) -> str:
        return (
            "Walmart exact-detail queue: "
            f"discovered **{self.discovered}** • unique item IDs this pass **{self.queued_unique}** • "
            f"due/pending **{self.pending_total}**"
        )


@dataclass(frozen=True)
class VerificationQueueBatchResult:
    claimed: int = 0
    verified: int = 0
    official_references: int = 0
    markdowns: int = 0
    no_reference: int = 0
    under_threshold: int = 0
    unavailable: int = 0
    identity_blocked: int = 0
    failed: int = 0
    pending_total: int = 0

    def summary_line(self) -> str:
        return (
            "Walmart background exact verification: "
            f"claimed **{self.claimed}** • verified **{self.verified}** • "
            f"official was prices **{self.official_references}** • markdowns **{self.markdowns}** • "
            f"no reference **{self.no_reference}** • under threshold **{self.under_threshold}** • "
            f"unavailable **{self.unavailable}** • identity blocked **{self.identity_blocked}** • "
            f"failed/retrying **{self.failed}** • due/pending **{self.pending_total}**"
        )


@dataclass(frozen=True)
class _QueueClaim:
    item_id: str
    title: str
    product_url: str
    image_url: str
    apparent_current_cents: int | None
    apparent_reference_cents: int | None
    route_hint: str
    lease_token: str
    attempt_count: int


async def ensure_walmart_exact_verification_queue(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {QUEUE_TABLE} (
            item_id TEXT PRIMARY KEY,
            priority_score INTEGER NOT NULL DEFAULT 0,
            apparent_current_cents INTEGER,
            apparent_reference_cents INTEGER,
            apparent_discount_bps INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            product_url TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            route_hint TEXT NOT NULL DEFAULT '',
            source_label TEXT NOT NULL DEFAULT '',
            discovered_count INTEGER NOT NULL DEFAULT 1,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT,
            next_attempt_at TEXT NOT NULL,
            last_error TEXT NOT NULL DEFAULT '',
            lease_token TEXT NOT NULL DEFAULT '',
            lease_until TEXT,
            verified_at TEXT,
            exact_current_cents INTEGER,
            exact_reference_cents INTEGER,
            exact_discount_bps INTEGER NOT NULL DEFAULT 0,
            exact_reference_source TEXT NOT NULL DEFAULT '',
            exact_offer_id TEXT NOT NULL DEFAULT '',
            exact_seller_key TEXT NOT NULL DEFAULT '',
            exact_variant_key TEXT NOT NULL DEFAULT '',
            exact_condition_key TEXT NOT NULL DEFAULT '',
            exact_fulfillment_key TEXT NOT NULL DEFAULT '',
            snapshot_json TEXT NOT NULL DEFAULT ''
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_due "
        f"ON {QUEUE_TABLE} (next_attempt_at, priority_score DESC)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_last_seen "
        f"ON {QUEUE_TABLE} (last_seen_at)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_verified "
        f"ON {QUEUE_TABLE} (verified_at, exact_discount_bps DESC)"
    )
    await conn.commit()


async def enqueue_walmart_exact_verification_candidates(
    db: Any,
    candidates: Iterable[SourceCandidate],
    *,
    min_discount: int,
    source_label: str,
) -> VerificationQueueEnqueueResult:
    """Compatibility entry point; production scans use the batched implementation."""

    from sniperplug.services.walmart_exact_verification_queue_bulk import (
        enqueue_walmart_exact_verification_candidates_bulk,
    )

    return await enqueue_walmart_exact_verification_candidates_bulk(
        db,
        candidates,
        min_discount=min_discount,
        source_label=source_label,
    )


async def record_inline_exact_verifications(
    db: Any,
    candidates: Iterable[SourceCandidate],
    *,
    min_discount: int,
) -> int:
    if db is None:
        return 0
    await ensure_walmart_exact_verification_queue(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    count = 0
    for candidate in exact_detail_verified_candidates(candidates):
        item_id = _candidate_item_id(candidate)
        if not item_id:
            continue
        await _ensure_candidate_row(conn, candidate, now=now)
        await _store_exact_candidate(
            conn,
            item_id=item_id,
            candidate=candidate,
            min_discount=min_discount,
            now=now,
            lease_token="",
        )
        count += 1
    if count:
        await conn.commit()
    return count


async def load_recent_verified_queue_candidates(
    db: Any,
    *,
    limit: int = 12,
    max_age_minutes: int = QUEUE_VERIFIED_SNAPSHOT_MAX_AGE_MINUTES,
) -> list[SourceCandidate]:
    if db is None or limit <= 0:
        return []
    await ensure_walmart_exact_verification_queue(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    verified_cutoff = (
        now - timedelta(minutes=max(1, int(max_age_minutes)))
    ).isoformat()
    discovery_cutoff = (now - timedelta(days=QUEUE_RETENTION_DAYS)).isoformat()
    cursor = await conn.execute(
        f"""
        SELECT item_id, verified_at, snapshot_json
        FROM {QUEUE_TABLE}
        WHERE verified_at IS NOT NULL
          AND verified_at >= ?
          AND last_seen_at >= ?
          AND snapshot_json <> ''
          AND status IN (
              'verified_markdown',
              'verified_under_threshold',
              'verified_no_reference',
              'verified_reference'
          )
        ORDER BY
            CASE status WHEN 'verified_markdown' THEN 0 ELSE 1 END,
            exact_discount_bps DESC,
            priority_score DESC,
            verified_at DESC
        LIMIT ?
        """,
        (verified_cutoff, discovery_cutoff, max(1, int(limit))),
    )
    rows = await cursor.fetchall()
    loaded: list[SourceCandidate] = []
    for row in rows:
        candidate = _candidate_from_snapshot(_row_get(row, "snapshot_json", index=2))
        if candidate is None or not exact_detail_verified_candidates([candidate]):
            continue
        attrs = dict(candidate.variant_attributes or {})
        attrs["verificationQueueSource"] = "global_exact_detail_queue"
        attrs["verificationQueueVerifiedAt"] = str(
            _row_get(row, "verified_at", index=1) or ""
        )
        candidate.variant_attributes = attrs
        loaded.append(candidate)
    return loaded


async def process_walmart_exact_verification_queue_batch(
    db: Any,
    *,
    provider: Any,
    limit: int = 6,
    concurrency: int = 2,
    min_discount: int = 50,
    timeout_seconds: float = 8.0,
) -> VerificationQueueBatchResult:
    if db is None or provider is None or limit <= 0:
        return VerificationQueueBatchResult()

    await ensure_walmart_exact_verification_queue(db)
    await ensure_global_offer_memory_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    await maybe_prune_walmart_exact_verification_queue(conn, now=now)
    claims = await _claim_due_rows(conn, now=now, limit=max(1, int(limit)))
    if not claims:
        return VerificationQueueBatchResult(
            pending_total=await _pending_total(conn, now_iso=now.isoformat())
        )

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def fetch(claim: _QueueClaim):
        async with semaphore:
            return claim, await _fetch_exact_candidate(
                provider,
                claim,
                timeout_seconds=timeout_seconds,
            )

    outcomes = await asyncio.gather(*(fetch(claim) for claim in claims))
    counts = {
        "verified": 0,
        "official_references": 0,
        "markdowns": 0,
        "no_reference": 0,
        "under_threshold": 0,
        "unavailable": 0,
        "identity_blocked": 0,
        "failed": 0,
    }

    for claim, outcome in outcomes:
        candidate, status, error = outcome
        item_now = datetime.now(timezone.utc)
        if candidate is None:
            await _store_failure(
                conn,
                claim=claim,
                status=status,
                error=error,
                now=item_now,
            )
            counts["failed"] += 1
            if status == "incomplete_identity":
                counts["identity_blocked"] += 1
            continue

        await _store_exact_candidate(
            conn,
            item_id=claim.item_id,
            candidate=candidate,
            min_discount=min_discount,
            now=item_now,
            lease_token=claim.lease_token,
        )
        counts["verified"] += 1
        if _trusted_reference(candidate) is not None:
            counts["official_references"] += 1
        classification = _classify_exact_candidate(candidate, min_discount=min_discount)
        if classification == "verified_markdown":
            counts["markdowns"] += 1
        elif classification == "verified_no_reference":
            counts["no_reference"] += 1
        elif classification == "verified_under_threshold":
            counts["under_threshold"] += 1
        elif classification == "not_buyable":
            counts["unavailable"] += 1

        identity = exact_offer_identity(candidate)
        if identity is not None:
            await observe_exact_offer(
                conn,
                candidate=candidate,
                identity=identity,
                now=item_now,
                min_discount=min_discount,
            )

    await maybe_prune_global_offer_memory(conn, now=datetime.now(timezone.utc))
    await conn.commit()
    pending_total = await _pending_total(
        conn,
        now_iso=datetime.now(timezone.utc).isoformat(),
    )
    return VerificationQueueBatchResult(
        claimed=len(claims),
        pending_total=pending_total,
        **counts,
    )


async def maybe_prune_walmart_exact_verification_queue(
    conn: Any,
    *,
    now: datetime | None = None,
) -> None:
    """Expire rows by last discovery, not by the worker's own rechecks."""

    global _last_cleanup_monotonic
    monotonic_now = time.monotonic()
    if monotonic_now - _last_cleanup_monotonic < QUEUE_CLEANUP_INTERVAL_SECONDS:
        return

    now_dt = now or datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(days=QUEUE_RETENTION_DAYS)).isoformat()
    await conn.execute(
        f"DELETE FROM {QUEUE_TABLE} WHERE last_seen_at < ?",
        (cutoff,),
    )
    await conn.execute(
        f"""
        DELETE FROM {QUEUE_TABLE}
        WHERE item_id IN (
            SELECT item_id
            FROM {QUEUE_TABLE}
            ORDER BY
                CASE WHEN status = 'verified_markdown' THEN 0 ELSE 1 END,
                priority_score DESC,
                last_seen_at DESC
            LIMIT -1 OFFSET ?
        )
        """,
        (QUEUE_MAX_ROWS,),
    )
    await conn.commit()
    _last_cleanup_monotonic = monotonic_now


async def _claim_due_rows(
    conn: Any,
    *,
    now: datetime,
    limit: int,
) -> list[_QueueClaim]:
    now_iso = now.isoformat()
    discovery_cutoff = (now - timedelta(days=QUEUE_RETENTION_DAYS)).isoformat()
    cursor = await conn.execute(
        f"""
        SELECT
            item_id, title, product_url, image_url,
            apparent_current_cents, apparent_reference_cents,
            route_hint, attempt_count
        FROM {QUEUE_TABLE}
        WHERE next_attempt_at <= ?
          AND last_seen_at >= ?
          AND (lease_until IS NULL OR lease_until < ?)
        ORDER BY
            CASE status
                WHEN 'pending' THEN 0
                WHEN 'retry' THEN 1
                WHEN 'failed' THEN 2
                ELSE 3
            END,
            priority_score DESC,
            last_seen_at DESC
        LIMIT ?
        """,
        (now_iso, discovery_cutoff, now_iso, max(1, int(limit))),
    )
    rows = await cursor.fetchall()
    claims: list[_QueueClaim] = []
    lease_until = (now + timedelta(seconds=QUEUE_LEASE_SECONDS)).isoformat()

    for row in rows:
        item_id = str(_row_get(row, "item_id", index=0) or "").strip()
        if not item_id:
            continue
        token = uuid.uuid4().hex
        await conn.execute(
            f"""
            UPDATE {QUEUE_TABLE}
            SET lease_token = ?, lease_until = ?, status = 'verifying'
            WHERE item_id = ?
              AND last_seen_at >= ?
              AND (lease_until IS NULL OR lease_until < ?)
            """,
            (token, lease_until, item_id, discovery_cutoff, now_iso),
        )
        verify = await conn.execute(
            f"SELECT lease_token FROM {QUEUE_TABLE} WHERE item_id = ?",
            (item_id,),
        )
        lease_row = await verify.fetchone()
        if str(_row_get(lease_row, "lease_token", index=0) or "") != token:
            continue
        claims.append(
            _QueueClaim(
                item_id=item_id,
                title=str(_row_get(row, "title", index=1) or ""),
                product_url=str(_row_get(row, "product_url", index=2) or ""),
                image_url=str(_row_get(row, "image_url", index=3) or ""),
                apparent_current_cents=_int_or_none(
                    _row_get(row, "apparent_current_cents", index=4)
                ),
                apparent_reference_cents=_int_or_none(
                    _row_get(row, "apparent_reference_cents", index=5)
                ),
                route_hint=str(_row_get(row, "route_hint", index=6) or ""),
                lease_token=token,
                attempt_count=int(_row_get(row, "attempt_count", index=7) or 0),
            )
        )
    await conn.commit()
    return claims


async def _fetch_exact_candidate(
    provider: Any,
    claim: _QueueClaim,
    *,
    timeout_seconds: float,
) -> tuple[SourceCandidate | None, str, str]:
    detail_fetcher = getattr(provider, "fetch_product_detail_payload", None)
    inner = getattr(provider, "inner", provider)
    candidate_builder = getattr(inner, "_candidate_from_item", None)
    if not callable(detail_fetcher) or not callable(candidate_builder):
        return None, "provider_unavailable", "exact Walmart detail provider unavailable"

    seed = _seed_candidate(claim)
    try:
        payload = await asyncio.wait_for(
            detail_fetcher(claim.item_id),
            timeout=max(0.1, float(timeout_seconds)),
        )
        exact = candidate_builder(
            payload,
            request=ProviderScanRequest(
                source_key="walmart_exact_detail_queue",
                query=claim.item_id,
                max_results=1,
                metadata={"exact_detail_price_check": "queue"},
            ),
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - queued provider failures remain retryable.
        return None, "retry", _compact_text(error, 500)

    if exact is None:
        return None, "retry", "exact Walmart detail response did not build a candidate"
    if _candidate_item_id(exact) != claim.item_id:
        return None, "identity_mismatch", "exact detail returned a different Walmart item ID"

    _apply_exact_payload_offer_identity(exact, payload=payload, item_id=claim.item_id)
    merged = _merge_exact_candidate(seed, exact, item_id=claim.item_id)
    if exact_offer_identity(merged) is None:
        return None, "incomplete_identity", (
            "exact detail did not provide a complete seller/offer identity"
        )
    if _positive_number(
        getattr(merged, "api_current_price", None)
        or getattr(merged, "current_price", None)
    ) is None:
        return None, "retry", "exact detail did not provide a numeric current price"
    return merged, "verified", ""


async def _ensure_candidate_row(
    conn: Any,
    candidate: SourceCandidate,
    *,
    now: datetime,
) -> None:
    item_id = _candidate_item_id(candidate)
    if not item_id:
        return
    current = _positive_number(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    reference = _trusted_reference(candidate)
    discount = _percent_off(current, reference) or 0.0
    attrs = dict(getattr(candidate, "variant_attributes", None) or {})
    await conn.execute(
        f"""
        INSERT INTO {QUEUE_TABLE} (
            item_id, priority_score, apparent_current_cents,
            apparent_reference_cents, apparent_discount_bps,
            title, product_url, image_url, route_hint, source_label,
            discovered_count, first_seen_at, last_seen_at,
            status, attempt_count, next_attempt_at
        ) VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, 'inline_exact', 1, ?, ?, 'pending', 0, ?)
        ON CONFLICT(item_id) DO NOTHING
        """,
        (
            item_id,
            _price_to_cents(current),
            _price_to_cents(reference),
            int(round(discount * 100)),
            _compact_text(getattr(candidate, "title", None), 300),
            _compact_text(
                getattr(candidate, "direct_product_url", None)
                or getattr(candidate, "product_url", None),
                1000,
            ),
            _compact_text(getattr(candidate, "image_url", None), 1000),
            _compact_text(
                attrs.get("finderSourceQuery")
                or attrs.get("finderSourceQueries")
                or "",
                240,
            ),
            now.isoformat(),
            now.isoformat(),
            now.isoformat(),
        ),
    )


async def _store_exact_candidate(
    conn: Any,
    *,
    item_id: str,
    candidate: SourceCandidate,
    min_discount: int,
    now: datetime,
    lease_token: str,
) -> None:
    identity = exact_offer_identity(candidate)
    if identity is None:
        return

    current = _positive_number(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    reference = _trusted_reference(candidate)
    discount = _percent_off(current, reference) or 0.0
    status = _classify_exact_candidate(candidate, min_discount=min_discount)
    next_attempt = (now + _recheck_delay(status)).isoformat()
    snapshot = _candidate_snapshot(candidate)

    params: list[Any] = [
        status,
        now.isoformat(),
        _price_to_cents(current),
        _price_to_cents(reference),
        int(round(discount * 100)),
        _compact_text(getattr(candidate, "api_reference_path", None), 300),
        identity.offer_id,
        identity.seller_key,
        identity.variant_key,
        identity.condition_key,
        identity.fulfillment_key,
        snapshot,
        now.isoformat(),
        next_attempt,
        item_id,
    ]
    if lease_token:
        where_guard = "lease_token = ?"
        params.append(lease_token)
    else:
        where_guard = "(lease_until IS NULL OR lease_until < ?)"
        params.append(now.isoformat())

    await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET status = ?,
            verified_at = ?,
            exact_current_cents = ?,
            exact_reference_cents = ?,
            exact_discount_bps = ?,
            exact_reference_source = ?,
            exact_offer_id = ?,
            exact_seller_key = ?,
            exact_variant_key = ?,
            exact_condition_key = ?,
            exact_fulfillment_key = ?,
            snapshot_json = ?,
            last_attempt_at = ?,
            attempt_count = attempt_count + 1,
            next_attempt_at = ?,
            last_error = '',
            lease_token = '',
            lease_until = NULL
        WHERE item_id = ? AND {where_guard}
        """,
        tuple(params),
    )


async def _store_failure(
    conn: Any,
    *,
    claim: _QueueClaim,
    status: str,
    error: str,
    now: datetime,
) -> None:
    attempts = max(1, claim.attempt_count + 1)
    if status in {"identity_mismatch", "incomplete_identity"}:
        delay_seconds = 12 * 60 * 60
        stored_status = status
    elif status == "provider_unavailable":
        delay_seconds = 30 * 60
        stored_status = "retry"
    else:
        delay_seconds = min(
            QUEUE_RETRY_MAX_SECONDS,
            QUEUE_RETRY_BASE_SECONDS * (2 ** min(6, attempts - 1)),
        )
        stored_status = "retry"
    await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET status = ?,
            attempt_count = attempt_count + 1,
            last_attempt_at = ?,
            next_attempt_at = ?,
            last_error = ?,
            lease_token = '',
            lease_until = NULL
        WHERE item_id = ? AND lease_token = ?
        """,
        (
            stored_status,
            now.isoformat(),
            (now + timedelta(seconds=delay_seconds)).isoformat(),
            _compact_text(error, 500),
            claim.item_id,
            claim.lease_token,
        ),
    )


def _classify_exact_candidate(
    candidate: SourceCandidate,
    *,
    min_discount: int,
) -> str:
    stock = str(getattr(candidate, "stock_status", None) or "").strip().lower()
    if getattr(candidate, "can_add_to_cart", None) is False or any(
        token in stock for token in ("out of stock", "unavailable", "sold out")
    ):
        return "not_buyable"

    current = _positive_number(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    reference = _trusted_reference(candidate)
    if reference is None:
        return "verified_no_reference"
    discount = _percent_off(current, reference)
    if discount is None:
        return "verified_reference"
    if discount >= max(1, int(min_discount)):
        return "verified_markdown"
    return "verified_under_threshold"


def _recheck_delay(status: str) -> timedelta:
    if status == "verified_markdown":
        return timedelta(hours=1)
    if status == "verified_no_reference":
        return timedelta(hours=6)
    if status == "verified_under_threshold":
        return timedelta(hours=12)
    if status == "not_buyable":
        return timedelta(hours=24)
    return timedelta(hours=12)


def _seed_candidate(claim: _QueueClaim) -> SourceCandidate:
    product_url = claim.product_url or f"https://www.walmart.com/ip/{claim.item_id}"
    attrs: dict[str, str] = {"verificationQueueSource": "global_exact_detail_queue"}
    if claim.route_hint:
        attrs["finderSourceQuery"] = claim.route_hint
    return SourceCandidate(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title=claim.title or f"Walmart item {claim.item_id}",
        product_url=product_url,
        direct_product_url=product_url,
        image_url=claim.image_url or None,
        current_price=_cents_to_price(claim.apparent_current_cents),
        typical_price=_cents_to_price(claim.apparent_reference_cents),
        api_current_price=_cents_to_price(claim.apparent_current_cents),
        api_reference_price=_cents_to_price(claim.apparent_reference_cents),
        product_id=claim.item_id,
        product_id_type="sku",
        sku=claim.item_id,
        selected_offer_id=claim.item_id,
        variant_attributes=attrs,
    )


def _candidate_snapshot(candidate: SourceCandidate) -> str:
    attrs = {
        _compact_text(key, 100): _compact_text(value, 400)
        for key, value in list(dict(candidate.variant_attributes or {}).items())[:60]
        if _compact_text(key, 100) and _compact_text(value, 400)
    }
    payload = {
        "source_key": "walmart_exact_detail_queue",
        "retailer": "Walmart",
        "title": _compact_text(candidate.title, 300),
        "product_url": _compact_text(candidate.product_url, 1000),
        "direct_product_url": _compact_text(candidate.direct_product_url, 1000),
        "image_url": _compact_text(candidate.image_url, 1000),
        "current_price": _positive_number(candidate.current_price),
        "typical_price": _positive_number(candidate.typical_price),
        "deal_lane": _compact_text(candidate.deal_lane, 80),
        "api_current_price": _positive_number(candidate.api_current_price),
        "api_reference_price": _positive_number(candidate.api_reference_price),
        "api_discount_percent": _positive_number(candidate.api_discount_percent),
        "api_condition": _compact_text(candidate.api_condition, 120),
        "api_condition_path": _compact_text(candidate.api_condition_path, 300),
        "api_reference_path": _compact_text(candidate.api_reference_path, 300),
        "api_price_path": _compact_text(candidate.api_price_path, 300),
        "product_id": _compact_text(candidate.product_id, 120),
        "product_id_type": _compact_text(candidate.product_id_type, 40),
        "sku": _compact_text(candidate.sku, 120),
        "upc": _compact_text(candidate.upc, 120),
        "selected_offer_id": _compact_text(candidate.selected_offer_id, 240),
        "variant_label": _compact_text(candidate.variant_label, 240),
        "variant_attributes": attrs,
        "pack_size": _compact_text(candidate.pack_size, 120),
        "color": _compact_text(candidate.color, 120),
        "platform": _compact_text(candidate.platform, 120),
        "model": _compact_text(candidate.model, 160),
        "parent_title": _compact_text(candidate.parent_title, 300),
        "option_mismatch_warning": _compact_text(candidate.option_mismatch_warning, 300),
        "seller_name": _compact_text(candidate.seller_name, 240),
        "fulfillment_type": _compact_text(candidate.fulfillment_type, 120),
        "condition": _compact_text(candidate.condition, 120),
        "stock_status": _compact_text(candidate.stock_status, 120),
        "can_add_to_cart": candidate.can_add_to_cart,
        "is_business_offer": bool(candidate.is_business_offer),
        "is_member_only": bool(candidate.is_member_only),
        "is_checkout_price": bool(candidate.is_checkout_price),
        "signals": [_compact_text(value, 300) for value in list(candidate.signals or [])[:12]],
        "first_seen_at": _compact_text(candidate.first_seen_at, 80),
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _candidate_from_snapshot(value: Any) -> SourceCandidate | None:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return SourceCandidate(
            source_key=str(payload.get("source_key") or "walmart_exact_detail_queue"),
            retailer="Walmart",
            title=str(payload.get("title") or "Walmart item"),
            product_url=str(payload.get("product_url") or payload.get("direct_product_url") or ""),
            direct_product_url=str(payload.get("direct_product_url") or payload.get("product_url") or ""),
            image_url=payload.get("image_url") or None,
            current_price=_positive_number(payload.get("current_price")),
            typical_price=_positive_number(payload.get("typical_price")),
            deal_lane=payload.get("deal_lane") or None,
            api_current_price=_positive_number(payload.get("api_current_price")),
            api_reference_price=_positive_number(payload.get("api_reference_price")),
            api_discount_percent=_positive_number(payload.get("api_discount_percent")),
            api_condition=payload.get("api_condition") or None,
            api_condition_path=payload.get("api_condition_path") or None,
            api_reference_path=payload.get("api_reference_path") or None,
            api_price_path=payload.get("api_price_path") or None,
            product_id=payload.get("product_id") or None,
            product_id_type=payload.get("product_id_type") or None,
            sku=payload.get("sku") or None,
            upc=payload.get("upc") or None,
            selected_offer_id=payload.get("selected_offer_id") or None,
            variant_label=payload.get("variant_label") or None,
            variant_attributes={
                str(key): str(item)
                for key, item in dict(payload.get("variant_attributes") or {}).items()
            },
            pack_size=payload.get("pack_size") or None,
            color=payload.get("color") or None,
            platform=payload.get("platform") or None,
            model=payload.get("model") or None,
            parent_title=payload.get("parent_title") or None,
            option_mismatch_warning=payload.get("option_mismatch_warning") or None,
            seller_name=payload.get("seller_name") or None,
            fulfillment_type=payload.get("fulfillment_type") or None,
            condition=payload.get("condition") or None,
            stock_status=payload.get("stock_status") or None,
            can_add_to_cart=payload.get("can_add_to_cart"),
            is_business_offer=bool(payload.get("is_business_offer")),
            is_member_only=bool(payload.get("is_member_only")),
            is_checkout_price=bool(payload.get("is_checkout_price")),
            signals=[
                str(item)
                for item in list(payload.get("signals") or [])[:12]
                if str(item or "").strip()
            ],
            first_seen_at=str(
                payload.get("first_seen_at") or datetime.now(timezone.utc).isoformat()
            ),
            last_checked_at=str(
                payload.get("last_checked_at") or datetime.now(timezone.utc).isoformat()
            ),
        )
    except (TypeError, ValueError):
        return None


async def _pending_total(conn: Any, *, now_iso: str) -> int:
    now = _parse_datetime(now_iso) or datetime.now(timezone.utc)
    discovery_cutoff = (now - timedelta(days=QUEUE_RETENTION_DAYS)).isoformat()
    cursor = await conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {QUEUE_TABLE}
        WHERE next_attempt_at <= ?
          AND last_seen_at >= ?
          AND (lease_until IS NULL OR lease_until < ?)
        """,
        (now_iso, discovery_cutoff, now_iso),
    )
    row = await cursor.fetchone()
    return int(_row_get(row, "COUNT(*)", index=0) or 0)


def _price_to_cents(value: Any) -> int | None:
    parsed = _positive_number(value)
    return int(round(parsed * 100)) if parsed is not None else None


def _cents_to_price(value: Any) -> float | None:
    parsed = _int_or_none(value)
    return round(parsed / 100.0, 2) if parsed is not None else None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[: max(0, int(limit))]


def _row_get(row: Any, key: str, *, index: int | None = None) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    if index is not None:
        try:
            return row[index]
        except Exception:
            pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)
