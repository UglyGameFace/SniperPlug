from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.services.walmart_exact_price_enrichment import (
    _apply_exact_payload_offer_identity,
    _candidate_item_id,
    _merge_exact_candidate,
    _positive_number,
    _trusted_reference,
)
from sniperplug.services.walmart_exact_queue_drain import (
    claim_due_rows_batched,
    store_exact_candidate_with_tiered_recheck,
)
from sniperplug.services.walmart_exact_queue_health import (
    load_walmart_exact_queue_health,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _classify_exact_candidate,
    _pending_total,
    _store_failure,
    ensure_walmart_exact_verification_queue,
    maybe_prune_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_global_offer_memory import (
    ensure_global_offer_memory_table,
    exact_offer_identity,
    maybe_prune_global_offer_memory,
    observe_exact_offer,
)


TERMINAL_IDENTITY_STATUSES = ("incomplete_identity", "identity_mismatch")
TERMINAL_IDENTITY_REPROBE_DAYS = 7
TERMINAL_NEXT_ATTEMPT = "9999-12-31T23:59:59+00:00"
DRAIN_ACTIONABLE_THRESHOLD = 450
DRAIN_BATCH_SIZE = 24
DRAIN_CONCURRENCY = 4


@dataclass(frozen=True)
class ExactQueueRuntimeResult:
    claimed: int = 0
    verified: int = 0
    official_references: int = 0
    markdowns: int = 0
    no_reference: int = 0
    under_threshold: int = 0
    unavailable: int = 0
    identity_blocked: int = 0
    identity_missing_seller: int = 0
    identity_missing_offer: int = 0
    identity_mismatch: int = 0
    identity_missing_proof: int = 0
    identity_other: int = 0
    failed: int = 0
    pending_total: int = 0
    terminal_quarantined: int = 0
    terminal_rearmed: int = 0
    mode: str = "normal"
    batch_size: int = 0
    concurrency: int = 0
    claim_seconds: float = 0.0
    fetch_seconds: float = 0.0
    store_seconds: float = 0.0

    def summary_line(self) -> str:
        identity_detail = (
            f"seller **{self.identity_missing_seller}** • "
            f"offer **{self.identity_missing_offer}** • "
            f"item mismatch **{self.identity_mismatch}** • "
            f"proof **{self.identity_missing_proof}** • "
            f"other **{self.identity_other}**"
        )
        timing = (
            f"claim **{self.claim_seconds:.2f}s** • "
            f"fetch **{self.fetch_seconds:.2f}s** • "
            f"store **{self.store_seconds:.2f}s**"
        )
        return (
            "Walmart background exact verification: "
            f"mode **{self.mode}** • batch/concurrency **{self.batch_size}/{self.concurrency}** • "
            f"claimed **{self.claimed}** • verified **{self.verified}** • "
            f"official was prices **{self.official_references}** • "
            f"markdowns **{self.markdowns}** • no reference **{self.no_reference}** • "
            f"under threshold **{self.under_threshold}** • unavailable **{self.unavailable}** • "
            f"identity unavailable / safely blocked **{self.identity_blocked}** "
            f"({identity_detail}) • transient failures **{self.failed}** • "
            f"terminal rows quarantined **{self.terminal_quarantined}** • "
            f"weekly identity reprobes **{self.terminal_rearmed}** • "
            f"actionable due **{self.pending_total}** • timings {timing}"
        )


@dataclass(frozen=True)
class TerminalIdentityMaintenance:
    quarantined: int = 0
    rearmed: int = 0


async def maintain_terminal_identity_rows(
    db: Any,
    *,
    now: datetime | None = None,
) -> TerminalIdentityMaintenance:
    """Keep terminal identity failures out of normal queue claims.

    A missing seller/offer identity is not a transient network failure. It stays
    fail-closed and does not consume another API call every twelve hours. A row
    may receive one low-frequency reprobe only after it was rediscovered after
    its last attempt and at least seven days have passed.
    """

    if db is None:
        return TerminalIdentityMaintenance()

    await ensure_walmart_exact_verification_queue(db)
    conn = db.require_conn()
    await _ensure_terminal_identity_indexes(conn)
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_iso = now_dt.astimezone(timezone.utc).isoformat()
    reprobe_cutoff = (
        now_dt.astimezone(timezone.utc)
        - timedelta(days=TERMINAL_IDENTITY_REPROBE_DAYS)
    ).isoformat()

    rearm_cursor = await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET status = 'pending',
            next_attempt_at = ?,
            lease_token = '',
            lease_until = NULL,
            last_error = CASE
                WHEN last_error = '' THEN 'scheduled weekly identity reprobe'
                ELSE last_error || ' | scheduled weekly identity reprobe'
            END
        WHERE status IN ('incomplete_identity', 'identity_mismatch')
          AND last_attempt_at IS NOT NULL
          AND last_attempt_at <= ?
          AND last_seen_at > last_attempt_at
        """,
        (now_iso, reprobe_cutoff),
    )
    rearm_count = await _affected_rows(conn, rearm_cursor)

    quarantine_cursor = await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET next_attempt_at = ?,
            lease_token = '',
            lease_until = NULL
        WHERE status IN ('incomplete_identity', 'identity_mismatch')
          AND next_attempt_at <= ?
        """,
        (TERMINAL_NEXT_ATTEMPT, now_iso),
    )
    quarantine_count = await _affected_rows(conn, quarantine_cursor)

    if rearm_count or quarantine_count:
        await conn.commit()
    return TerminalIdentityMaintenance(
        quarantined=quarantine_count,
        rearmed=rearm_count,
    )


async def process_actionable_walmart_exact_queue_batch(
    db: Any,
    *,
    provider: Any,
    limit: int = 6,
    concurrency: int = 2,
    min_discount: int = 50,
    timeout_seconds: float = 8.0,
) -> ExactQueueRuntimeResult:
    if db is None or provider is None or limit <= 0:
        return ExactQueueRuntimeResult()

    maintenance = await maintain_terminal_identity_rows(db)
    await ensure_walmart_exact_verification_queue(db)
    await ensure_global_offer_memory_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    await maybe_prune_walmart_exact_verification_queue(conn, now=now)

    health_before = await load_walmart_exact_queue_health(db)
    drain_mode = int(health_before.due_now) >= DRAIN_ACTIONABLE_THRESHOLD
    effective_limit = max(1, int(limit))
    effective_concurrency = max(1, int(concurrency))
    if drain_mode:
        effective_limit = max(effective_limit, DRAIN_BATCH_SIZE)
        effective_concurrency = max(effective_concurrency, DRAIN_CONCURRENCY)
    mode = "drain" if drain_mode else "normal"

    claim_started = time.monotonic()
    claims = await claim_due_rows_batched(
        conn,
        now=now,
        limit=effective_limit,
    )
    claim_seconds = max(0.0, time.monotonic() - claim_started)
    if not claims:
        return ExactQueueRuntimeResult(
            pending_total=await _pending_total(conn, now_iso=now.isoformat()),
            terminal_quarantined=maintenance.quarantined,
            terminal_rearmed=maintenance.rearmed,
            mode=mode,
            batch_size=effective_limit,
            concurrency=effective_concurrency,
            claim_seconds=claim_seconds,
        )

    semaphore = asyncio.Semaphore(effective_concurrency)

    async def fetch(claim):
        async with semaphore:
            return claim, await _fetch_exact_candidate_off_loop(
                provider,
                claim,
                timeout_seconds=timeout_seconds,
            )

    fetch_started = time.monotonic()
    outcomes = await asyncio.gather(*(fetch(claim) for claim in claims))
    fetch_seconds = max(0.0, time.monotonic() - fetch_started)
    counts = {
        "verified": 0,
        "official_references": 0,
        "markdowns": 0,
        "no_reference": 0,
        "under_threshold": 0,
        "unavailable": 0,
        "identity_blocked": 0,
        "identity_missing_seller": 0,
        "identity_missing_offer": 0,
        "identity_mismatch": 0,
        "identity_missing_proof": 0,
        "identity_other": 0,
        "failed": 0,
    }

    store_started = time.monotonic()
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
            if status in TERMINAL_IDENTITY_STATUSES:
                counts["identity_blocked"] += 1
                bucket = _identity_error_bucket(status=status, error=error)
                counts[bucket] += 1
            else:
                counts["failed"] += 1
            continue

        await store_exact_candidate_with_tiered_recheck(
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
    store_seconds = max(0.0, time.monotonic() - store_started)
    pending_total = await _pending_total(
        conn,
        now_iso=datetime.now(timezone.utc).isoformat(),
    )
    return ExactQueueRuntimeResult(
        claimed=len(claims),
        pending_total=pending_total,
        terminal_quarantined=maintenance.quarantined,
        terminal_rearmed=maintenance.rearmed,
        mode=mode,
        batch_size=effective_limit,
        concurrency=effective_concurrency,
        claim_seconds=claim_seconds,
        fetch_seconds=fetch_seconds,
        store_seconds=store_seconds,
        **counts,
    )


async def _fetch_exact_candidate_off_loop(
    provider: Any,
    claim: Any,
    *,
    timeout_seconds: float,
) -> tuple[SourceCandidate | None, str, str]:
    detail_fetcher = getattr(provider, "fetch_product_detail_payload", None)
    inner = getattr(provider, "inner", provider)
    candidate_builder = getattr(inner, "_candidate_from_item", None)
    if not callable(detail_fetcher) or not callable(candidate_builder):
        return None, "provider_unavailable", "exact Walmart detail provider unavailable"

    try:
        payload = await asyncio.wait_for(
            detail_fetcher(claim.item_id),
            timeout=max(0.1, float(timeout_seconds)),
        )
        request = ProviderScanRequest(
            source_key="walmart_exact_detail_queue",
            query=claim.item_id,
            max_results=1,
            metadata={"exact_detail_price_check": "queue"},
        )
        exact = await asyncio.to_thread(
            candidate_builder,
            payload,
            request=request,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - provider failures remain retryable.
        return None, "retry", _compact_text(error, 500)

    if exact is None:
        return None, "retry", "exact Walmart detail response did not build a candidate"
    if _candidate_item_id(exact) != claim.item_id:
        return None, "identity_mismatch", "exact item identity mismatch"

    merged = await asyncio.to_thread(
        _prepare_exact_candidate,
        claim,
        exact,
        payload,
    )
    identity = exact_offer_identity(merged)
    if identity is None:
        return None, "incomplete_identity", _exact_identity_failure_reason(merged)
    if _positive_number(
        getattr(merged, "api_current_price", None)
        or getattr(merged, "current_price", None)
    ) is None:
        return None, "retry", "exact detail did not provide a numeric current price"
    return merged, "verified", ""


def _prepare_exact_candidate(claim: Any, exact: SourceCandidate, payload: Any) -> SourceCandidate:
    from sniperplug.services.walmart_exact_verification_queue import _seed_candidate

    _apply_exact_payload_offer_identity(exact, payload=payload, item_id=claim.item_id)
    return _merge_exact_candidate(
        _seed_candidate(claim),
        exact,
        item_id=claim.item_id,
    )


def _exact_identity_failure_reason(candidate: SourceCandidate) -> str:
    attrs = dict(getattr(candidate, "variant_attributes", None) or {})
    if str(attrs.get("exactDetailPriceProof") or "").strip().lower() != "yes":
        return "missing exact price proof"

    item_id = _candidate_item_id(candidate)
    exact_item_id = str(attrs.get("exactDetailItemId") or "").strip()
    if not item_id or not exact_item_id or item_id != exact_item_id:
        return "exact item identity mismatch"

    offer_id = str(getattr(candidate, "selected_offer_id", None) or "").strip()
    if not offer_id or offer_id.lower() in {"none", "unknown", "null"}:
        return "missing selected offer identity"

    seller_id = str(attrs.get("sellerId") or "").strip()
    seller_name = str(
        getattr(candidate, "seller_name", None)
        or attrs.get("seller")
        or ""
    ).strip()
    walmart_seller = str(attrs.get("walmartSeller") or "").strip().lower() == "yes"
    if not seller_id and not seller_name and not walmart_seller:
        return "missing seller identity"

    return "incomplete exact offer identity"


def _identity_error_bucket(*, status: str, error: str) -> str:
    lowered = str(error or "").lower()
    if status == "identity_mismatch" or "item identity mismatch" in lowered:
        return "identity_mismatch"
    if "seller" in lowered:
        return "identity_missing_seller"
    if "offer" in lowered:
        return "identity_missing_offer"
    if "price proof" in lowered:
        return "identity_missing_proof"
    return "identity_other"


async def _ensure_terminal_identity_indexes(conn: Any) -> None:
    if getattr(conn, "_sniperplug_terminal_identity_indexes_ready", False):
        return
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_terminal_rearm "
        f"ON {QUEUE_TABLE} (status, last_attempt_at, last_seen_at)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{QUEUE_TABLE}_terminal_quarantine "
        f"ON {QUEUE_TABLE} (status, next_attempt_at)"
    )
    await conn.commit()
    try:
        setattr(conn, "_sniperplug_terminal_identity_indexes_ready", True)
    except Exception:
        pass


async def _affected_rows(conn: Any, cursor: Any) -> int:
    try:
        rowcount = int(cursor.rowcount)
    except (AttributeError, TypeError, ValueError):
        rowcount = -1
    if rowcount >= 0:
        return rowcount

    changes = await conn.execute("SELECT changes()")
    row = await changes.fetchone()
    if row is None:
        return 0
    try:
        return int(row[0] or 0)
    except (TypeError, ValueError, KeyError, IndexError):
        try:
            return int(row["changes()"] or 0)
        except Exception:
            return 0


def _compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[: max(0, int(limit))]
