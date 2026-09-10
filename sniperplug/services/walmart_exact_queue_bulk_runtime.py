from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sniperplug.services.walmart_exact_fetch_pacing import (
    fetch_exact_candidate_with_page_pacing,
    page_html_probe_remaining_seconds,
    _page_challenge_circuit_open,
    _page_challenge_circuit_remaining_seconds,
)
from sniperplug.services.walmart_exact_price_enrichment import _trusted_reference
from sniperplug.services.walmart_exact_queue_bulk_persistence import (
    ExactQueuePersistenceOutcome,
    persist_exact_queue_outcomes_bulk,
)
from sniperplug.services.walmart_exact_queue_drain import (
    PAGE_RECOVERY_CLAIM_CAP,
    claim_due_rows_batched,
)
from sniperplug.services.walmart_exact_queue_pressure import (
    load_walmart_exact_queue_pressure,
)
from sniperplug.services.walmart_exact_queue_runtime import (
    DRAIN_BATCH_SIZE,
    DRAIN_CONCURRENCY,
    EXACT_FETCH_HARD_TIMEOUT_SECONDS,
    TERMINAL_IDENTITY_STATUSES,
    ExactQueueRuntimeResult,
    _fresh_snapshot_rejection_reason,
    _identity_error_bucket,
    _is_fresh_snapshot_candidate,
    _retry_error_bucket,
    _retry_kind_from_error,
)
from sniperplug.services.walmart_exact_runtime_schema import (
    ensure_exact_runtime_schema_once,
    maintain_terminal_identity_rows_bounded,
)
from sniperplug.services.walmart_exact_verification_queue import (
    _classify_exact_candidate,
)
from sniperplug.services.walmart_fresh_work_policy import should_use_drain_mode
from sniperplug.services.walmart_global_offer_memory import (
    maybe_prune_global_offer_memory,
)


log = logging.getLogger("sniperplug.autoscan.write_telemetry")

# The exact worker only needs to detect the fresh-work drain threshold (48).
# A 64-row cap leaves headroom while preventing the 20-second worker from
# rereading hundreds/thousands of scheduled rechecks before and after each batch.
EXACT_WORKER_PRESSURE_CAP = 64

# The HTML challenge circuit must stop Walmart product-page requests, not stop
# official API recovery entirely. Keep the already-bounded page lane alive at the
# same two-row cap while the circuit is open; the paced shared fetcher then forces
# those claims through official detail/Product Lookup only and never calls the
# challenged page loader. When the circuit is closed, HTML remains a last-resort
# single-probe fallback while fresh/API proof keeps normal worker throughput.
API_ONLY_PAGE_RECOVERY_CLAIM_CAP = min(2, PAGE_RECOVERY_CLAIM_CAP)


def _recovery_kind_from_error(error: str) -> str:
    """Read the secondary exact-lookup recovery outcome without changing retry policy."""

    for token in str(error or "").split():
        if not token.startswith("recovery_kind="):
            continue
        value = token.split("=", 1)[1].strip().strip(",;:()[]{}")
        if value:
            return value[:80]
    return ""


def _retry_reason_label(error: str) -> str:
    primary = _retry_kind_from_error(error)
    recovery = _recovery_kind_from_error(error)
    if recovery:
        return f"{primary}->{recovery}"
    return primary


async def process_actionable_walmart_exact_queue_batch(
    db: Any,
    *,
    provider: Any,
    limit: int = 6,
    concurrency: int = 2,
    min_discount: int = 50,
    timeout_seconds: float = EXACT_FETCH_HARD_TIMEOUT_SECONDS,
) -> ExactQueueRuntimeResult:
    """Run exact verification with bounded bulk persistence.

    Discovery, item identity, selected offer, seller, condition, fulfillment,
    current-price, and trusted-reference proof remain unchanged. Only the final
    Turso persistence path is consolidated so a batch does not issue dozens of
    serialized native libsql operations.

    Large 24/4 drain batches are reserved strictly for substantial unclaimed
    first-time/retry pressure. Scheduled rechecks never force drain mode. Legacy
    4/12-hour verified timestamps are filtered at claim time until the shared
    24-hour fallback age has actually elapsed, so cadence migration costs zero
    repair writes. A material catalog change reclassifies the row as ``pending``
    and wakes it immediately.

    Exact queue and offer-memory schema initialization is cached per live
    database connection. Terminal identity maintenance is bounded to a
    fifteen-minute cadence because terminal rows are already excluded from
    ordinary claims immediately. Scheduling decisions use a tightly bounded
    pressure snapshot rather than rereading the scheduled-recheck backlog.

    Walmart HTML is isolated from normal exact throughput. Fresh catalog proof
    and official item-detail/Product Lookup run first for every claim. Only one
    claim per pacing window may attempt the product-page fallback, and the first
    challenge-class response opens the existing provider circuit immediately.
    While that circuit is open, a tiny two-row page-recovery lane remains
    claimable because those rows still get official API exact recovery without
    touching HTML.

    A page-retry row is already known to require recovery. While the single HTML
    probe slot is merely paced (rather than challenge-blocked), do not lease old
    page-retry rows that can only be deferred again. Fresh/pending work still gets
    its official API attempt at normal throughput. This removes pointless lease
    and outcome writes without weakening proof or suppressing newly complete API
    identity.

    Queue retention cleanup is deliberately not performed here. This function
    returns as soon as verification state is durable so the caller can fan out
    verified deals before any maintenance DELETE is allowed to use the shared
    process-isolated Turso worker.
    """

    if db is None or provider is None or limit <= 0:
        return ExactQueueRuntimeResult()

    await ensure_exact_runtime_schema_once(db)
    maintenance = await maintain_terminal_identity_rows_bounded(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)

    page_circuit_remaining = _page_challenge_circuit_remaining_seconds(provider)
    page_probe_remaining = page_html_probe_remaining_seconds(provider)

    pressure_before = await load_walmart_exact_queue_pressure(
        db,
        cap=EXACT_WORKER_PRESSURE_CAP,
    )
    drain_mode = should_use_drain_mode(pressure_before)
    effective_limit = max(1, int(limit))
    effective_concurrency = max(1, int(concurrency))
    if drain_mode:
        effective_limit = max(effective_limit, DRAIN_BATCH_SIZE)
        effective_concurrency = max(effective_concurrency, DRAIN_CONCURRENCY)
    mode = "drain" if drain_mode else "normal"

    if page_circuit_remaining > 0:
        page_recovery_claim_cap = API_ONLY_PAGE_RECOVERY_CLAIM_CAP
    elif page_probe_remaining > 0:
        # Existing page_retry rows already failed official identity once. Until
        # the one-per-minute HTML slot is available, leasing them only produces
        # another page_pacing_deferred write. New/retry rows remain claimable and
        # may still succeed from current official API data.
        page_recovery_claim_cap = 0
    else:
        page_recovery_claim_cap = PAGE_RECOVERY_CLAIM_CAP

    claim_started = time.monotonic()
    claims = await claim_due_rows_batched(
        conn,
        now=now,
        limit=effective_limit,
        page_recovery_claim_cap=page_recovery_claim_cap,
    )
    claim_seconds = max(0.0, time.monotonic() - claim_started)
    if not claims:
        return ExactQueueRuntimeResult(
            pending_total=pressure_before.due_now,
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
            return claim, await fetch_exact_candidate_with_page_pacing(
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

    persistence_outcomes: list[ExactQueuePersistenceOutcome] = []
    retry_reason_counts: dict[str, int] = {}
    fresh_rejection_counts: dict[str, int] = {}
    fresh_snapshot_inputs = sum(
        1
        for claim in claims
        if str(getattr(claim, "discovery_snapshot_json", "") or "")
    )
    page_circuit_suppressed = 0

    for claim, outcome in outcomes:
        candidate, status, error = outcome
        observed_at = datetime.now(timezone.utc)
        persistence_outcomes.append(
            ExactQueuePersistenceOutcome(
                claim=claim,
                candidate=candidate,
                status=status,
                error=error,
                observed_at=observed_at,
            )
        )

        is_fresh = _is_fresh_snapshot_candidate(candidate)
        if str(getattr(claim, "discovery_snapshot_json", "") or "") and not is_fresh:
            reason = _fresh_snapshot_rejection_reason(claim, now=observed_at)
            fresh_rejection_counts[reason] = fresh_rejection_counts.get(reason, 0) + 1

        retry_kind = _retry_kind_from_error(error)
        if retry_kind in {"page_circuit_open", "page_pacing_deferred"}:
            page_circuit_suppressed += 1

        if candidate is None:
            if status in TERMINAL_IDENTITY_STATUSES:
                counts["identity_blocked"] += 1
                bucket = _identity_error_bucket(status=status, error=error)
                counts[bucket] += 1
            else:
                counts["failed"] += 1
                counts[_retry_error_bucket(status=status, error=error)] += 1
                retry_reason = _retry_reason_label(error)
                retry_reason_counts[retry_reason] = retry_reason_counts.get(retry_reason, 0) + 1
            continue

        counts["verified"] += 1
        if is_fresh:
            counts["fresh_catalog_verified"] += 1
        if _trusted_reference(candidate) is not None:
            counts["official_references"] += 1
        classification = _classify_exact_candidate(
            candidate,
            min_discount=min_discount,
        )
        if classification == "verified_markdown":
            counts["markdowns"] += 1
        elif classification == "verified_no_reference":
            counts["no_reference"] += 1
        elif classification == "verified_under_threshold":
            counts["under_threshold"] += 1
        elif classification == "not_buyable":
            counts["unavailable"] += 1

    store_started = time.monotonic()
    persisted = await persist_exact_queue_outcomes_bulk(
        conn,
        persistence_outcomes,
        min_discount=min_discount,
    )
    if persisted.queue_rows != len(claims):
        raise RuntimeError(
            "Bulk Walmart queue persistence did not prepare every claimed row: "
            f"prepared={persisted.queue_rows} claimed={len(claims)}"
        )
    await maybe_prune_global_offer_memory(
        conn,
        now=datetime.now(timezone.utc),
    )
    await conn.commit()
    store_seconds = max(0.0, time.monotonic() - store_started)
    log.info(
        "Walmart exact Turso write telemetry lease_rows=%s outcome_rows=%s "
        "offer_memory_rows=%s persistence_statements=%s "
        "page_circuit_reschedule_rows=0 price_recovery=exact_id_search_v257 "
        "exact_lookup=affiliate_items_ids_v276 "
        "confirmed_no_price_cooldown=api_price_missing_v277",
        len(claims),
        persisted.queue_rows,
        persisted.offer_rows,
        persisted.sql_statements,
    )
    pressure_after = await load_walmart_exact_queue_pressure(
        db,
        cap=EXACT_WORKER_PRESSURE_CAP,
    )
    return ExactQueueRuntimeResult(
        claimed=len(claims),
        pending_total=pressure_after.due_now,
        terminal_quarantined=maintenance.quarantined,
        terminal_rearmed=maintenance.rearmed,
        mode=mode,
        batch_size=effective_limit,
        concurrency=effective_concurrency,
        claim_seconds=claim_seconds,
        fetch_seconds=fetch_seconds,
        store_seconds=store_seconds,
        retry_reasons=tuple(
            sorted(
                retry_reason_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:6]
        ),
        fresh_snapshot_inputs=fresh_snapshot_inputs,
        fresh_snapshot_rejections=tuple(
            sorted(
                fresh_rejection_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:6]
        ),
        page_circuit_open=_page_challenge_circuit_open(provider),
        page_circuit_suppressed=page_circuit_suppressed,
        page_circuit_remaining_seconds=_page_challenge_circuit_remaining_seconds(provider),
        **counts,
    )
