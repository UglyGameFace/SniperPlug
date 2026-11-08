from __future__ import annotations

import asyncio
import json
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
    PAGE_RETRY_STATUS,
    QUEUE_TABLE,
    _classify_exact_candidate,
    _pending_total,
    _store_failure,
    ensure_walmart_exact_verification_queue,
    maybe_prune_walmart_exact_verification_queue,
    page_observed_price,
)
from sniperplug.services.walmart_global_offer_memory import (
    ensure_global_offer_memory_table,
    exact_offer_identity,
    maybe_prune_global_offer_memory,
    observe_exact_offer,
)
from sniperplug.services.walmart_fresh_discovery_proof import (
    DISCOVERY_PROOF_MAX_AGE_SECONDS,
    DISCOVERY_PROOF_VERSION,
    promote_fresh_discovery_offer_snapshot,
)


TERMINAL_IDENTITY_STATUSES = ("incomplete_identity", "identity_mismatch")
TERMINAL_IDENTITY_REPROBE_DAYS = 7
TERMINAL_NEXT_ATTEMPT = "9999-12-31T23:59:59+00:00"
DRAIN_ACTIONABLE_THRESHOLD = 450
DRAIN_BATCH_SIZE = 24
DRAIN_CONCURRENCY = 4
EXACT_FETCH_HARD_TIMEOUT_SECONDS = 50.0

# Live production repeatedly returned whole 8/8 page_challenge batches. Per-row
# one-hour retry delays do not prevent a worker from immediately challenging a
# different row. Open a provider-scoped circuit after two challenge-class page
# responses inside two minutes; while open, keep using official API/exact-search
# recovery but suppress Walmart HTML for fifteen minutes. The circuit never
# creates proof and never changes an eligibility gate.
PAGE_CHALLENGE_CIRCUIT_THRESHOLD = 2
PAGE_CHALLENGE_CIRCUIT_WINDOW_SECONDS = 2 * 60.0
PAGE_CHALLENGE_CIRCUIT_COOLDOWN_SECONDS = 15 * 60.0
_PAGE_CHALLENGE_RETRY_KINDS = frozenset(
    {"page_challenge", "page_http_403", "page_http_429"}
)
_PAGE_CIRCUIT_STATE_ATTR = "_sniperplug_page_challenge_circuit_state"


@dataclass(frozen=True)
class ExactQueueRuntimeResult:
    claimed: int = 0
    verified: int = 0
    fresh_catalog_verified: int = 0
    fresh_snapshot_inputs: int = 0
    fresh_snapshot_rejections: tuple[tuple[str, int], ...] = ()
    official_references: int = 0
    markdowns: int = 0
    page_price: int = 0
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
    retry_page: int = 0
    retry_api: int = 0
    retry_timeout: int = 0
    retry_price_change: int = 0
    retry_candidate: int = 0
    retry_other: int = 0
    retry_reasons: tuple[tuple[str, int], ...] = ()
    page_circuit_open: bool = False
    page_circuit_suppressed: int = 0
    page_circuit_remaining_seconds: int = 0
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
        retry_detail = (
            f"page **{self.retry_page}** • api **{self.retry_api}** • "
            f"timeout **{self.retry_timeout}** • price-change **{self.retry_price_change}** • "
            f"candidate **{self.retry_candidate}** • other **{self.retry_other}**"
        )
        timing = (
            f"claim **{self.claim_seconds:.2f}s** • "
            f"fetch **{self.fetch_seconds:.2f}s** • "
            f"store **{self.store_seconds:.2f}s**"
        )
        retry_reasons = ""
        if self.retry_reasons:
            retry_reasons = " • retry kinds " + ", ".join(
                f"**{name}={count}**" for name, count in self.retry_reasons[:6]
            )
        snapshot_rejections = ""
        if self.fresh_snapshot_rejections:
            snapshot_rejections = " • fresh proof rejects " + ", ".join(
                f"**{name}={count}**"
                for name, count in self.fresh_snapshot_rejections[:6]
            )
        circuit = "open" if self.page_circuit_open else "closed"
        circuit_detail = (
            f" • page circuit **{circuit}** • page suppressed **{self.page_circuit_suppressed}**"
        )
        if self.page_circuit_open and self.page_circuit_remaining_seconds > 0:
            circuit_detail += f" • circuit retry in **{self.page_circuit_remaining_seconds}s**"
        return (
            "Walmart background exact verification: "
            f"mode **{self.mode}** • batch/concurrency **{self.batch_size}/{self.concurrency}** • "
            f"claimed **{self.claimed}** • verified **{self.verified}** • "
            f"fresh catalog proof **{self.fresh_catalog_verified}**/{self.fresh_snapshot_inputs} • "
            f"official was prices **{self.official_references}** • "
            f"markdowns **{self.markdowns}** • page-price **{self.page_price}** • no reference **{self.no_reference}** • "
            f"under threshold **{self.under_threshold}** • unavailable **{self.unavailable}** • "
            f"identity unavailable / safely blocked **{self.identity_blocked}** "
            f"({identity_detail}) • transient failures **{self.failed}** ({retry_detail})"
            f"{retry_reasons}{snapshot_rejections}{circuit_detail} • "
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
    timeout_seconds: float = EXACT_FETCH_HARD_TIMEOUT_SECONDS,
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
            page_circuit_open=_page_challenge_circuit_open(provider),
            page_circuit_remaining_seconds=_page_challenge_circuit_remaining_seconds(provider),
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
        "fresh_catalog_verified": 0,
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
        "retry_page": 0,
        "retry_api": 0,
        "retry_timeout": 0,
        "retry_price_change": 0,
        "retry_candidate": 0,
        "retry_other": 0,
    }
    fresh_snapshot_inputs = sum(
        1 for claim in claims if str(getattr(claim, "discovery_snapshot_json", "") or "")
    )
    fresh_rejections: dict[str, int] = {}
    page_circuit_suppressed = 0

    store_started = time.monotonic()
    for claim, outcome in outcomes:
        candidate, status, error = outcome
        item_now = datetime.now(timezone.utc)
        is_fresh = _is_fresh_snapshot_candidate(candidate)
        if str(getattr(claim, "discovery_snapshot_json", "") or "") and not is_fresh:
            reason = _fresh_snapshot_rejection_reason(claim, now=item_now)
            fresh_rejections[reason] = fresh_rejections.get(reason, 0) + 1
        if _retry_kind_from_error(error) == "page_circuit_open":
            page_circuit_suppressed += 1

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
                counts[_retry_error_bucket(status=status, error=error)] += 1
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
        if is_fresh:
            counts["fresh_catalog_verified"] += 1
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
        fresh_snapshot_inputs=fresh_snapshot_inputs,
        fresh_snapshot_rejections=tuple(
            sorted(fresh_rejections.items(), key=lambda item: (-item[1], item[0]))[:6]
        ),
        page_circuit_open=_page_challenge_circuit_open(provider),
        page_circuit_suppressed=page_circuit_suppressed,
        page_circuit_remaining_seconds=_page_challenge_circuit_remaining_seconds(provider),
        **counts,
    )


async def _fetch_exact_candidate_off_loop(
    provider: Any,
    claim: Any,
    *,
    timeout_seconds: float,
) -> tuple[SourceCandidate | None, str, str]:
    fresh = promote_fresh_discovery_offer_snapshot(
        str(getattr(claim, "discovery_snapshot_json", "") or ""),
        snapshot_at=getattr(claim, "discovery_snapshot_at", None),
        item_id=str(getattr(claim, "item_id", "") or ""),
        apparent_current_cents=getattr(claim, "apparent_current_cents", None),
    )
    if fresh is not None:
        return fresh, "verified", ""

    inner = getattr(provider, "inner", provider)
    detail_fetcher = getattr(provider, "fetch_product_detail_payload", None)
    candidate_builder = getattr(inner, "_candidate_from_item", None)
    bypass_page = False
    if _page_challenge_circuit_open(provider):
        official_detail_fetcher = getattr(inner, "_official_detail_payload", None)
        if callable(official_detail_fetcher):
            detail_fetcher = official_detail_fetcher
            bypass_page = True
    if not callable(detail_fetcher) or not callable(candidate_builder):
        return None, "provider_unavailable", "exact Walmart detail provider unavailable"

    try:
        payload = await asyncio.wait_for(
            detail_fetcher(claim.item_id),
            timeout=max(1.0, float(timeout_seconds)),
        )
        exact = await _candidate_from_payload_off_loop(
            candidate_builder,
            payload,
            item_id=claim.item_id,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return (
            None,
            "retry",
            f"retry_kind=outer_timeout exact Walmart detail exceeded {float(timeout_seconds):.1f}s hard safety budget",
        )
    except Exception as error:  # noqa: BLE001 - provider failures remain retryable.
        retry_kind = str(getattr(error, "retry_kind", "") or "").strip().lower()
        if _is_page_challenge_retry_kind(retry_kind):
            _record_page_challenge(provider, retry_kind)
        status = PAGE_RETRY_STATUS if retry_kind.startswith("page_") else "retry"
        kind = retry_kind or "provider_exception"
        return None, status, f"retry_kind={kind} {_compact_text(error, 440)}"

    if exact is None:
        return (
            None,
            "retry",
            "retry_kind=candidate_build exact Walmart detail response did not build a candidate",
        )
    if _candidate_item_id(exact) != claim.item_id:
        return None, "identity_mismatch", "exact item identity mismatch"

    merged = await asyncio.to_thread(
        _prepare_exact_candidate,
        claim,
        exact,
        payload,
    )
    identity = exact_offer_identity(merged)

    if identity is None and bypass_page:
        search_fetcher = getattr(inner, "_official_exact_search_payload", None)
        already_exact_search = (
            str(payload.get("sniperplugOfficialExactSource") or "").strip()
            == "affiliate_search_exact_item"
            if isinstance(payload, dict)
            else False
        )
        search_error_text = ""
        if callable(search_fetcher) and not already_exact_search:
            try:
                search_payload = await asyncio.wait_for(
                    search_fetcher(claim.item_id),
                    timeout=max(1.0, float(timeout_seconds)),
                )
                search_exact = await _candidate_from_payload_off_loop(
                    candidate_builder,
                    search_payload,
                    item_id=claim.item_id,
                )
                if search_exact is not None and _candidate_item_id(search_exact) == claim.item_id:
                    merged_search = await asyncio.to_thread(
                        _prepare_exact_candidate,
                        claim,
                        search_exact,
                        search_payload,
                    )
                    if exact_offer_identity(merged_search) is not None:
                        merged = merged_search
                        identity = exact_offer_identity(merged_search)
            except asyncio.CancelledError:
                raise
            except Exception as search_error:  # noqa: BLE001 - remains a retry diagnostic.
                search_kind = str(
                    getattr(search_error, "retry_kind", "") or "api_exact_search"
                ).strip().lower()
                search_error_text = (
                    f"; exact_search={search_kind}:{_compact_text(search_error, 180)}"
                )

        if identity is None:
            return (
                None,
                PAGE_RETRY_STATUS,
                "retry_kind=page_circuit_open Walmart product-page recovery suppressed "
                "during active challenge cooldown; official exact seller/offer proof remained incomplete"
                + search_error_text,
            )

    if identity is None:
        return None, "incomplete_identity", _exact_identity_failure_reason(merged)
    if _positive_number(
        getattr(merged, "api_current_price", None)
        or getattr(merged, "current_price", None)
    ) is None:
        if page_observed_price(merged) is not None:
            # The official API returned no numeric price, but the product
            # page confirmed seller/offer/item identity and exposed a price.
            # Keep the candidate as a page-price lead instead of parking it
            # as an api_price_missing retry; classification routes it to the
            # page-price lane and never to a markdown.
            return merged, "verified", ""
        return None, "retry", "retry_kind=api_price_missing exact detail did not provide a numeric current price"
    return merged, "verified", ""


async def _candidate_from_payload_off_loop(
    candidate_builder: Any,
    payload: Any,
    *,
    item_id: str,
) -> SourceCandidate | None:
    request = ProviderScanRequest(
        source_key="walmart_exact_detail_queue",
        query=item_id,
        max_results=1,
        metadata={"exact_detail_price_check": "queue"},
    )
    return await asyncio.to_thread(
        candidate_builder,
        payload,
        request=request,
    )


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
    page_context = _page_recovery_failure_context(attrs)
    if str(attrs.get("exactDetailPriceProof") or "").strip().lower() != "yes":
        return "missing exact price proof" + page_context

    item_id = _candidate_item_id(candidate)
    exact_item_id = str(attrs.get("exactDetailItemId") or "").strip()
    if not item_id or not exact_item_id or item_id != exact_item_id:
        return "exact item identity mismatch" + page_context

    offer_id = str(getattr(candidate, "selected_offer_id", None) or "").strip()
    if not offer_id or offer_id.lower() in {"none", "unknown", "null"}:
        return "missing selected offer identity" + page_context

    seller_id = str(attrs.get("sellerId") or "").strip()
    seller_name = str(
        getattr(candidate, "seller_name", None)
        or attrs.get("seller")
        or ""
    ).strip()
    walmart_seller = str(attrs.get("walmartSeller") or "").strip().lower() == "yes"
    if not seller_id and not seller_name and not walmart_seller:
        return "missing seller identity" + page_context

    return "incomplete exact offer identity" + page_context


def _page_recovery_failure_context(attrs: dict[str, Any]) -> str:
    status = str(attrs.get("pageIdentityRecoveryStatus") or "").strip()
    if not status:
        return ""
    detail = str(attrs.get("pageIdentityRecoveryDetail") or "").strip()
    context = f"; page recovery={status}"
    if detail:
        context += f" ({_compact_text(detail, 220)})"
    return context


def _retry_kind_from_error(error: str) -> str:
    for token in str(error or "").split():
        if not token.startswith("retry_kind="):
            continue
        value = token.split("=", 1)[1].strip().strip(",;:()[]{}")
        if value:
            return value[:80]
    return "unclassified"


def _retry_error_bucket(*, status: str, error: str) -> str:
    lowered = str(error or "").lower()
    if "page_price_mismatch" in lowered:
        return "retry_price_change"
    if status == PAGE_RETRY_STATUS or "retry_kind=page_" in lowered:
        return "retry_page"
    if "timeout" in lowered:
        return "retry_timeout"
    if "retry_kind=api_" in lowered:
        return "retry_api"
    if "candidate_build" in lowered:
        return "retry_candidate"
    return "retry_other"


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


def _is_fresh_snapshot_candidate(candidate: SourceCandidate | None) -> bool:
    if candidate is None:
        return False
    return (
        str(
            dict(getattr(candidate, "variant_attributes", None) or {}).get(
                "exactDetailPriceProofSource"
            )
            or ""
        ).strip()
        == "affiliate_catalog_fresh_snapshot"
    )


def _fresh_snapshot_rejection_reason(
    claim: Any,
    *,
    now: datetime | None = None,
) -> str:
    raw = str(getattr(claim, "discovery_snapshot_json", "") or "")
    if not raw:
        return "absent"
    observed = _parse_iso_datetime(getattr(claim, "discovery_snapshot_at", None))
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    if observed is None:
        return "timestamp_missing"
    age_seconds = (
        current_time.astimezone(timezone.utc) - observed.astimezone(timezone.utc)
    ).total_seconds()
    if age_seconds < -60:
        return "future_timestamp"
    if age_seconds > DISCOVERY_PROOF_MAX_AGE_SECONDS:
        return "stale"
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "invalid_json"
    if not isinstance(payload, dict) or payload.get("version") != DISCOVERY_PROOF_VERSION:
        return "version"
    item_id = str(getattr(claim, "item_id", "") or "").strip()
    if str(payload.get("item_id") or "").strip() != item_id:
        return "item_mismatch"
    apparent_cents = getattr(claim, "apparent_current_cents", None)
    snapshot_current = _positive_number(payload.get("current_price"))
    if snapshot_current is None or apparent_cents is None:
        return "price_missing"
    if int(round(snapshot_current * 100)) != int(apparent_cents):
        return "price_mismatch"
    offer_id = str(payload.get("selected_offer_id") or "").strip()
    seller = str(payload.get("seller_name") or "").strip()
    if not offer_id:
        return "offer_missing"
    if not seller:
        return "seller_missing"
    attrs = dict(payload.get("attrs") or {})
    marketplace = _explicit_bool(
        attrs.get("selectedOfferMarketplace"),
        attrs.get("isMarketPlaceItem"),
        attrs.get("isMarketplaceItem"),
        attrs.get("marketplace"),
    )
    if marketplace is True or not _seller_is_walmart(seller):
        shipping_status = str(attrs.get("selectedOfferShippingStatus") or "").strip().lower()
        delivered = _positive_number(attrs.get("selectedOfferDeliveredPrice"))
        if shipping_status not in {"free", "paid"} or delivered is None:
            return "shipping_incomplete"
        if int(round(delivered * 100)) != int(apparent_cents):
            return "delivered_price_mismatch"
    return "strict_guard"


def _page_challenge_circuit_open(provider: Any, *, now_monotonic: float | None = None) -> bool:
    _owner, state = _page_circuit_state(provider, create=False)
    if state is None:
        return False
    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    until = float(state.get("until", 0.0) or 0.0)
    if until <= now_value:
        if until:
            state["until"] = 0.0
            state["events"] = []
        return False
    return True


def _page_challenge_circuit_remaining_seconds(provider: Any) -> int:
    _owner, state = _page_circuit_state(provider, create=False)
    if state is None:
        return 0
    remaining = float(state.get("until", 0.0) or 0.0) - time.monotonic()
    return max(0, int(round(remaining)))


def _record_page_challenge(provider: Any, retry_kind: str) -> None:
    kind = str(retry_kind or "").strip().lower()
    if not _is_page_challenge_retry_kind(kind):
        return
    owner, state = _page_circuit_state(provider, create=True)
    if owner is None or state is None:
        return
    now_value = time.monotonic()
    events = [
        float(value)
        for value in list(state.get("events") or [])
        if now_value - float(value) <= PAGE_CHALLENGE_CIRCUIT_WINDOW_SECONDS
    ]
    events.append(now_value)
    state["events"] = events[-8:]
    if len(events) >= PAGE_CHALLENGE_CIRCUIT_THRESHOLD:
        state["until"] = max(
            float(state.get("until", 0.0) or 0.0),
            now_value + PAGE_CHALLENGE_CIRCUIT_COOLDOWN_SECONDS,
        )


def _page_circuit_state(
    provider: Any,
    *,
    create: bool,
) -> tuple[Any | None, dict[str, Any] | None]:
    inner = getattr(provider, "inner", provider)
    # Only the page-recovery provider exposes the API-only bypass. Do not attach
    # a circuit to arbitrary provider doubles that cannot safely suppress HTML.
    if not callable(getattr(inner, "_official_detail_payload", None)):
        return None, None
    state = getattr(inner, _PAGE_CIRCUIT_STATE_ATTR, None)
    if not isinstance(state, dict) and create:
        state = {"events": [], "until": 0.0}
        try:
            setattr(inner, _PAGE_CIRCUIT_STATE_ATTR, state)
        except Exception:
            return None, None
    return inner, state if isinstance(state, dict) else None


def _is_page_challenge_retry_kind(retry_kind: str) -> bool:
    return str(retry_kind or "").strip().lower() in _PAGE_CHALLENGE_RETRY_KINDS


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _explicit_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"yes", "true", "1"}:
            return True
        if text in {"no", "false", "0"}:
            return False
    return None


def _seller_is_walmart(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "walmart",
        "walmart.com",
        "walmart stores inc",
        "walmart stores, inc.",
    }


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
