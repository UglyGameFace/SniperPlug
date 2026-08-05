from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_price_enrichment import (
    _candidate_item_id,
    _enrichment_priority,
    _percent_off,
    _positive_number,
    _trusted_reference,
)
from sniperplug.services.walmart_exact_queue_pressure import (
    load_walmart_exact_queue_pressure,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    VerificationQueueEnqueueResult,
    _compact_text,
    _price_to_cents,
    ensure_walmart_exact_verification_queue,
    maybe_prune_walmart_exact_verification_queue,
)


QUEUE_UPSERT_CHUNK_SIZE = 40
_QUEUE_COLUMNS_PER_ROW = 16


async def enqueue_walmart_exact_verification_candidates_bulk(
    db: Any,
    candidates: Iterable[SourceCandidate],
    *,
    min_discount: int,
    source_label: str,
) -> VerificationQueueEnqueueResult:
    """Persist globally deduplicated discovery leads in bounded SQL batches.

    Search prices remain prioritization hints only. Batching is important for
    Turso because its async adapter serializes each remote execute operation.
    Forty rows use 640 parameters, safely below SQLite's historical 999-variable
    limit while reducing hundreds of remote writes to a handful of statements.
    The post-enqueue pressure summary is bounded as well; catalog discovery must
    never run a full exact-queue count on the serialized remote connection.
    """

    if db is None:
        return VerificationQueueEnqueueResult()

    await ensure_walmart_exact_verification_queue(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    unique: dict[str, SourceCandidate] = {}
    discovered = 0

    for candidate in candidates:
        discovered += 1
        item_id = _candidate_item_id(candidate)
        if not item_id:
            continue
        previous = unique.get(item_id)
        if previous is None or _priority(candidate, min_discount=min_discount) > _priority(
            previous,
            min_discount=min_discount,
        ):
            unique[item_id] = candidate

    rows = [
        _queue_row(
            item_id,
            candidate,
            min_discount=min_discount,
            source_label=source_label,
            now_iso=now_iso,
        )
        for item_id, candidate in unique.items()
    ]

    for offset in range(0, len(rows), QUEUE_UPSERT_CHUNK_SIZE):
        chunk = rows[offset : offset + QUEUE_UPSERT_CHUNK_SIZE]
        if not chunk:
            continue
        placeholders = ",".join(
            "(" + ",".join("?" for _ in range(_QUEUE_COLUMNS_PER_ROW)) + ")"
            for _ in chunk
        )
        params = tuple(value for row in chunk for value in row)
        await conn.execute(
            f"""
            INSERT INTO {QUEUE_TABLE} (
                item_id, priority_score, apparent_current_cents,
                apparent_reference_cents, apparent_discount_bps,
                title, product_url, image_url, route_hint, source_label,
                discovered_count, first_seen_at, last_seen_at, status,
                attempt_count, next_attempt_at
            ) VALUES {placeholders}
            ON CONFLICT(item_id) DO UPDATE SET
                priority_score = CASE
                    WHEN excluded.priority_score > priority_score
                    THEN excluded.priority_score ELSE priority_score END,
                apparent_current_cents = COALESCE(
                    excluded.apparent_current_cents,
                    apparent_current_cents
                ),
                apparent_reference_cents = COALESCE(
                    excluded.apparent_reference_cents,
                    apparent_reference_cents
                ),
                apparent_discount_bps = CASE
                    WHEN excluded.apparent_discount_bps > apparent_discount_bps
                    THEN excluded.apparent_discount_bps ELSE apparent_discount_bps END,
                title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE title END,
                product_url = CASE
                    WHEN excluded.product_url <> '' THEN excluded.product_url ELSE product_url END,
                image_url = CASE
                    WHEN excluded.image_url <> '' THEN excluded.image_url ELSE image_url END,
                route_hint = CASE
                    WHEN excluded.route_hint <> '' THEN excluded.route_hint ELSE route_hint END,
                source_label = excluded.source_label,
                discovered_count = discovered_count + 1,
                last_seen_at = excluded.last_seen_at,
                next_attempt_at = CASE
                    WHEN status IN ('pending', 'retry', 'failed')
                    THEN MIN(next_attempt_at, excluded.next_attempt_at)
                    ELSE next_attempt_at END
            """,
            params,
        )

    await conn.commit()
    await maybe_prune_walmart_exact_verification_queue(conn, now=now)
    pressure = await load_walmart_exact_queue_pressure(db)
    return VerificationQueueEnqueueResult(
        discovered=discovered,
        queued_unique=len(unique),
        pending_total=pressure.due_now,
    )


def _queue_row(
    item_id: str,
    candidate: SourceCandidate,
    *,
    min_discount: int,
    source_label: str,
    now_iso: str,
) -> tuple[Any, ...]:
    current = _positive_number(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    reference = _trusted_reference(candidate)
    discount = _percent_off(current, reference) or 0.0
    attrs = dict(getattr(candidate, "variant_attributes", None) or {})
    route_hint = _compact_text(
        attrs.get("finderSourceQuery")
        or attrs.get("finderSourceQueries")
        or "",
        240,
    )
    return (
        item_id,
        int(round(_priority(candidate, min_discount=min_discount) * 100)),
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
        route_hint,
        _compact_text(source_label, 120),
        1,
        now_iso,
        now_iso,
        "pending",
        0,
        now_iso,
    )


def _priority(candidate: SourceCandidate, *, min_discount: int) -> float:
    try:
        return float(_enrichment_priority(candidate, min_discount=min_discount))
    except Exception:
        return 0.0
