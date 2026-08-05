from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    _row_get,
    maybe_prune_walmart_exact_verification_queue,
)


# Sixteen parameters per row keeps sixty-row writes below SQLite's conservative
# historical 999-variable ceiling while reducing a 75-100 item catalog pass to
# at most two writes when every row is genuinely new or changed.
QUEUE_UPSERT_CHUNK_SIZE = 60
QUEUE_EXISTING_LOOKUP_CHUNK_SIZE = 500
QUEUE_DISCOVERY_REFRESH_INTERVAL_SECONDS = 6 * 60 * 60
_QUEUE_COLUMNS_PER_ROW = 16
_REARM_STATUSES = frozenset({"pending", "retry", "failed"})


@dataclass(frozen=True)
class _ExistingQueueRow:
    priority_score: int
    apparent_current_cents: int | None
    apparent_reference_cents: int | None
    apparent_discount_bps: int
    title: str
    product_url: str
    image_url: str
    route_hint: str
    source_label: str
    last_seen_at: str
    status: str
    next_attempt_at: str


@dataclass(frozen=True)
class _BoundedVerificationQueueEnqueueResult(VerificationQueueEnqueueResult):
    """Enqueue result whose pressure and write telemetry are bounded."""

    persisted_rows: int = 0
    unchanged_rows: int = 0
    write_statements: int = 0

    def summary_line(self) -> str:
        return (
            "Walmart exact-detail queue: "
            f"discovered **{self.discovered}** • unique item IDs this pass **{self.queued_unique}** • "
            f"persisted/unchanged **{self.persisted_rows}/{self.unchanged_rows}** • "
            f"write statements **{self.write_statements}** • "
            f"actionable due (bounded) **{self.pending_total}**"
        )


async def enqueue_walmart_exact_verification_candidates_bulk(
    db: Any,
    candidates: Iterable[SourceCandidate],
    *,
    min_discount: int,
    source_label: str,
) -> VerificationQueueEnqueueResult:
    """Persist globally deduplicated discovery leads without write amplification.

    Search prices remain prioritization hints only. Production catalog passes
    repeatedly rediscover many of the same item IDs, so the enqueue path first
    loads one bounded projection for those primary keys and writes only rows
    that are new, materially changed, due for a pending/retry rearm, or old
    enough to need a retention heartbeat. Schema initialization is shared with
    the exact worker's once-per-connection cache.

    Sixty rows use 960 parameters, below SQLite's conservative historical
    999-variable limit. A normal 75-100 row pass therefore uses no writes when
    every row is a recent unchanged rediscovery, and at most two statements
    when the complete pass is genuinely new or changed.
    """

    if db is None:
        return VerificationQueueEnqueueResult()

    # Import locally to keep the compatibility queue module's local bulk import
    # acyclic while still sharing one schema cache with the exact worker.
    from sniperplug.services.walmart_exact_runtime_schema import (
        ensure_exact_runtime_schema_once,
    )

    await ensure_exact_runtime_schema_once(db)
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
    existing = await _load_existing_queue_rows(
        conn,
        [str(row[0]) for row in rows],
    )
    refresh_cutoff = (
        now - timedelta(seconds=QUEUE_DISCOVERY_REFRESH_INTERVAL_SECONDS)
    ).isoformat()
    rows_to_write = [
        row
        for row in rows
        if _queue_row_needs_write(
            row,
            existing.get(str(row[0])),
            refresh_cutoff=refresh_cutoff,
        )
    ]

    write_statements = 0
    for offset in range(0, len(rows_to_write), QUEUE_UPSERT_CHUNK_SIZE):
        chunk = rows_to_write[offset : offset + QUEUE_UPSERT_CHUNK_SIZE]
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
        write_statements += 1

    if write_statements:
        await conn.commit()
    await maybe_prune_walmart_exact_verification_queue(conn, now=now)
    pressure = await load_walmart_exact_queue_pressure(db)
    return _BoundedVerificationQueueEnqueueResult(
        discovered=discovered,
        queued_unique=len(unique),
        pending_total=pressure.due_now,
        persisted_rows=len(rows_to_write),
        unchanged_rows=max(0, len(rows) - len(rows_to_write)),
        write_statements=write_statements,
    )


async def _load_existing_queue_rows(
    conn: Any,
    item_ids: list[str],
) -> dict[str, _ExistingQueueRow]:
    existing: dict[str, _ExistingQueueRow] = {}
    for offset in range(0, len(item_ids), QUEUE_EXISTING_LOOKUP_CHUNK_SIZE):
        chunk = item_ids[offset : offset + QUEUE_EXISTING_LOOKUP_CHUNK_SIZE]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        cursor = await conn.execute(
            f"""
            SELECT
                item_id, priority_score, apparent_current_cents,
                apparent_reference_cents, apparent_discount_bps,
                title, product_url, image_url, route_hint, source_label,
                last_seen_at, status, next_attempt_at
            FROM {QUEUE_TABLE}
            WHERE item_id IN ({placeholders})
            """,
            tuple(chunk),
        )
        for row in await cursor.fetchall():
            item_id = str(_row_get(row, "item_id", index=0) or "").strip()
            if not item_id:
                continue
            existing[item_id] = _ExistingQueueRow(
                priority_score=_int_value(
                    _row_get(row, "priority_score", index=1)
                ),
                apparent_current_cents=_optional_int(
                    _row_get(row, "apparent_current_cents", index=2)
                ),
                apparent_reference_cents=_optional_int(
                    _row_get(row, "apparent_reference_cents", index=3)
                ),
                apparent_discount_bps=_int_value(
                    _row_get(row, "apparent_discount_bps", index=4)
                ),
                title=str(_row_get(row, "title", index=5) or ""),
                product_url=str(_row_get(row, "product_url", index=6) or ""),
                image_url=str(_row_get(row, "image_url", index=7) or ""),
                route_hint=str(_row_get(row, "route_hint", index=8) or ""),
                source_label=str(_row_get(row, "source_label", index=9) or ""),
                last_seen_at=str(_row_get(row, "last_seen_at", index=10) or ""),
                status=str(_row_get(row, "status", index=11) or ""),
                next_attempt_at=str(
                    _row_get(row, "next_attempt_at", index=12) or ""
                ),
            )
    return existing


def _queue_row_needs_write(
    row: tuple[Any, ...],
    existing: _ExistingQueueRow | None,
    *,
    refresh_cutoff: str,
) -> bool:
    if existing is None:
        return True

    if int(row[1] or 0) > existing.priority_score:
        return True
    if row[2] is not None and int(row[2]) != existing.apparent_current_cents:
        return True
    if row[3] is not None and int(row[3]) != existing.apparent_reference_cents:
        return True
    if int(row[4] or 0) > existing.apparent_discount_bps:
        return True

    for incoming, stored in (
        (str(row[5] or ""), existing.title),
        (str(row[6] or ""), existing.product_url),
        (str(row[7] or ""), existing.image_url),
        (str(row[8] or ""), existing.route_hint),
    ):
        if incoming and incoming != stored:
            return True

    if str(row[9] or "") != existing.source_label:
        return True
    if not existing.last_seen_at or existing.last_seen_at <= refresh_cutoff:
        return True
    if (
        existing.status in _REARM_STATUSES
        and (
            not existing.next_attempt_at
            or existing.next_attempt_at > str(row[15] or "")
        )
    ):
        return True
    return False


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


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None
