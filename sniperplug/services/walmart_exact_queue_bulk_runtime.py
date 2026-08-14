from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sniperplug.services.walmart_exact_price_enrichment import _trusted_reference
from sniperplug.services.walmart_exact_queue_bulk_persistence import (
    ExactQueuePersistenceOutcome,
    persist_exact_queue_outcomes_bulk,
)
from sniperplug.services.walmart_exact_queue_drain import (
    PAGE_RECOVERY_CLAIM_CAP,
    claim_due_rows_batched,
)
from sniperplug.services.walmart_exact_queue_maintenance import (
    maybe_prune_walmart_exact_queue_bounded,
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
    _fetch_exact_candidate_off_loop,
    _fresh_snapshot_rejection_reason,
    _identity_error_bucket,
    _is_fresh_snapshot_candidate,
    _page_challenge_circuit_open,
    _page_challenge_circuit_remaining_seconds,
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
from sniperplug.services.walmart_recheck_schedule_rearm import (
    rearm_legacy_due_rechecks_bounded,
)


log = logging.getLogger("sniperplug.autoscan.write_telemetry")

# The exact worker only needs to detect the fresh-work drain threshold (48).
# A 64-row cap leaves headroom while preventing the 20-second worker from
# rereading hundreds/thousands of scheduled rechecks before and after each batch.
EXACT_WORKER_PRESSURE_CAP = 64


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
    first-time/retry pressure. Scheduled rechecks never force drain mode. Existing
    rows left on the old hourly schedule are repaired in bounded batches, and a
    row is deferred only when its own verified timestamp proves the current
    4/12/24-hour tier has not elapsed yet.

    Exact queue and offer-memory schema initialization is cached per live
    database connection. Terminal identity maintenance is bounded to a
    fifteen-minute cadence because terminal rows are already excluded from
    ordinary claims immediately. Scheduling decisions use a tightly bounded
    pressure snapshot rather than rereading the scheduled-recheck backlog.

    When the Walmart product-page challenge circuit is open, the claim selector
    sets the page-recovery lane cap to zero. Already-known ``page_retry`` rows are
    therefore left untouched instead of rewriting batches of 64 next-attempt
    timestamps every circuit. Recovery pressure is bounded at 64 for diagnostics
    and cannot by itself hit the 600-row catalog emergency stop. Fresh/pending/API
    work remains claimable, and a newly safe catalog snapshot can still change a
    page-retry row to ``pending`` immediately.

    Fresh discovery proof and page-challenge circuit state are reported directly
    in the worker summary. This makes a zero-promotion batch explainable without
    weakening proof: the log now distinguishes no snapshot input, strict snapshot
    rejection, ordinary API/page retries, and page requests suppressed by the
    challenge circuit.
    """

    if db is None or provider is None or limit <= 0:
        return ExactQueueRuntimeResult()

    await ensure_exact_runtime_schema_once(db)
    maintenance = await maintain_terminal_identity_rows_bounded(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    await maybe_prune_walmart_exact_queue_bounded(conn, now=now)
    await rearm_legacy_due_rechecks_bounded(conn, now=now)

    page_circuit_remaining = _page_challenge_circuit_remaining_seconds(provider)

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
    page_recovery_claim_cap = (
        0 if page_circuit_remaining > 0 else PAGE_RECOVERY_CLAIM_CAP
    )

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
        if retry_kind == "page_circuit_open":
            page_circuit_suppressed += 1

        if candidate is None:
            if status in TERMINAL_IDENTITY_STATUSES:
                counts["identity_blocked"] += 1
                bucket = _identity_error_bucket(status=status, error=error)
                counts[bucket] += 1
            else:
                counts["failed"] += 1
                counts[_retry_error_bucket(status=status, error=error)] += 1
                retry_reason_counts[retry_kind] = retry_reason_counts.get(retry_kind, 0) + 1
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
        "page_circuit_reschedule_rows=0",
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
