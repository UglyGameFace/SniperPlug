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
from sniperplug.services.walmart_exact_queue_health import (
    load_walmart_exact_queue_health,
)
from sniperplug.services.walmart_exact_queue_runtime import (
    DRAIN_ACTIONABLE_THRESHOLD,
    DRAIN_BATCH_SIZE,
    DRAIN_CONCURRENCY,
    TERMINAL_IDENTITY_STATUSES,
    ExactQueueRuntimeResult,
    _fetch_exact_candidate_off_loop,
    _identity_error_bucket,
    maintain_terminal_identity_rows,
)
from sniperplug.services.walmart_exact_verification_queue import (
    _classify_exact_candidate,
    _pending_total,
    ensure_walmart_exact_verification_queue,
    maybe_prune_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_global_offer_memory import (
    ensure_global_offer_memory_table,
    maybe_prune_global_offer_memory,
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
    """Run exact verification with bounded bulk persistence.

    Discovery, item identity, selected offer, seller, condition, fulfillment,
    current-price, and trusted-reference proof remain unchanged. Only the final
    Turso persistence path is consolidated so a drain batch does not issue
    dozens of serialized native libsql operations.
    """

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

    persistence_outcomes: list[ExactQueuePersistenceOutcome] = []
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
            continue

        counts["verified"] += 1
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
