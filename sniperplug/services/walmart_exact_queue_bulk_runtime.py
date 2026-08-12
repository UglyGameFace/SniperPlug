from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from sniperplug.services.walmart_exact_price_enrichment import _trusted_reference
from sniperplug.services.walmart_exact_queue_bulk_persistence import (
    ExactQueuePersistenceOutcome,
    persist_exact_queue_outcomes_bulk,
)
from sniperplug.services.walmart_exact_queue_drain import claim_due_rows_batched
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
    _identity_error_bucket,
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
    """

    if db is None or provider is None or limit <= 0:
        return ExactQueueRuntimeResult()

    await ensure_exact_runtime_schema_once(db)
    maintenance = await maintain_terminal_identity_rows_bounded(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    await maybe_prune_walmart_exact_queue_bounded(conn, now=now)
    await rearm_legacy_due_rechecks_bounded(conn, now=now)

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

    claim_started = time.monotonic()
    claims = await claim_due_rows_batched(
        conn,
        now=now,
        limit=effective_limit,
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
        if candidate is None:
            if status in TERMINAL_IDENTITY_STATUSES:
                counts["identity_blocked"] += 1
                bucket = _identity_error_bucket(status=status, error=error)
                counts[bucket] += 1
            else:
                counts["failed"] += 1
                counts[_retry_error_bucket(status=status, error=error)] += 1
                retry_kind = _retry_kind_from_error(error)
                retry_reason_counts[retry_kind] = retry_reason_counts.get(retry_kind, 0) + 1
            continue

        counts["verified"] += 1
        if (
            str(
                dict(getattr(candidate, "variant_attributes", None) or {}).get(
                    "exactDetailPriceProofSource"
                )
                or ""
            ).strip()
            == "affiliate_catalog_fresh_snapshot"
        ):
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
        **counts,
    )
