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
from sniperplug.services.walmart_fresh_discovery_proof import (
    DISCOVERY_PROOF_MAX_AGE_SECONDS,
    DISCOVERY_PROOF_REFRESH_INTERVAL_SECONDS,
    build_fresh_discovery_offer_snapshot,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    VerificationQueueEnqueueResult,
    _compact_text,
    _is_confirmed_api_price_missing,
    _price_to_cents,
    _row_get,
)


# Eighteen parameters per row keeps 55-row writes below SQLite's conservative
# historical 999-variable ceiling while preserving bounded selected-offer proof.
QUEUE_UPSERT_CHUNK_SIZE = 55
QUEUE_EXISTING_LOOKUP_CHUNK_SIZE = 500
# Queue retention is fourteen days. A once-per-day discovery heartbeat keeps a
# mature unchanged row comfortably inside that boundary without rewriting it on
# every two-hour-ish global route rotation.
QUEUE_DISCOVERY_REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
_QUEUE_COLUMNS_PER_ROW = 18
_REARM_STATUSES = frozenset({"pending", "retry", "failed"})
_TERMINAL_IDENTITY_REARM_STATUSES = frozenset(
    {"incomplete_identity", "identity_mismatch"}
)
_VERIFIED_REARM_STATUSES = frozenset(
    {
        "verified_markdown",
        "verified_under_threshold",
        "verified_no_reference",
        "verified_reference",
        "not_buyable",
    }
)


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
    last_error: str
    discovery_snapshot_json: str
    discovery_snapshot_at: str


@dataclass(frozen=True)
class _BoundedVerificationQueueEnqueueResult(VerificationQueueEnqueueResult):
    """Enqueue result whose pressure and write telemetry are bounded."""

    persisted_rows: int = 0
    unchanged_rows: int = 0
    write_statements: int = 0
    safe_snapshot_rows: int = 0
    page_retry_rearmed: int = 0
    terminal_identity_rearmed: int = 0
    verified_change_rearmed: int = 0
    snapshot_refresh_skipped: int = 0

    def summary_line(self) -> str:
        return (
            "Walmart exact-detail queue: "
            f"discovered **{self.discovered}** • unique item IDs this pass **{self.queued_unique}** • "
            f"safe fresh proof **{self.safe_snapshot_rows}** • "
            f"page retries rescued **{self.page_retry_rearmed}** • "
            f"terminal identity rescued **{self.terminal_identity_rearmed}** • "
            f"verified changes rearmed **{self.verified_change_rearmed}** • "
            f"stale proof refresh skipped **{self.snapshot_refresh_skipped}** • "
            f"persisted/unchanged **{self.persisted_rows}/{self.unchanged_rows}** • "
            f"write statements **{self.write_statements}** • "
            f"actionable due (bounded) **{self.pending_total}**"
        )




def prefer_discovery_offer(
    proposed: SourceCandidate,
    selected: SourceCandidate,
    *,
    min_discount: int,
) -> bool:
    """Choose a promising *observation* for one item without losing price glitches.

    One durable queue row exists per Walmart item ID. For different observed
    offers/variants, prioritize non-confirmed-unbuyable inventory, then the
    cheaper payable price. If tied, retain the stronger existing discovery
    priority. Neither catalog observation qualifies for a public alert until
    exact offer/seller/stock and price proof succeeds.
    """
    from sniperplug.services.stock_status import is_unavailable_stock

    def known_unbuyable(candidate: SourceCandidate) -> bool:
        attrs = getattr(candidate, "variant_attributes", None) or {}
        online = str(attrs.get("availableOnline") or "").strip().lower()
        return (
            getattr(candidate, "can_add_to_cart", None) is False
            or is_unavailable_stock(getattr(candidate, "stock_status", None))
            or online in {"no", "false", "0", "off"}
        )

    proposed_bad = known_unbuyable(proposed)
    selected_bad = known_unbuyable(selected)
    if proposed_bad != selected_bad:
        return not proposed_bad

    def price(candidate: SourceCandidate) -> float | None:
        return _positive_number(
            getattr(candidate, "api_current_price", None)
            or getattr(candidate, "current_price", None)
        )

    new_price, old_price = price(proposed), price(selected)
    if new_price is not None and old_price is None:
        return True
    if old_price is not None and new_price is None:
        return False
    if new_price is not None and old_price is not None and round(new_price * 100) != round(old_price * 100):
        return new_price < old_price

    # The exact queue's stable priority is still the tie-break. Preserve the
    # first observation for equal price/priority to avoid needless writes.
    return _priority(proposed, min_discount=min_discount) > _priority(
        selected, min_discount=min_discount
    )

async def enqueue_walmart_exact_verification_candidates_bulk(
    db: Any,
    candidates: Iterable[SourceCandidate],
    *,
    min_discount: int,
    source_label: str,
) -> VerificationQueueEnqueueResult:
    """Persist globally deduplicated discovery leads without write amplification.

    Ordinary discovery hints remain prioritization-only. When a catalog result
    contains a complete atomic selected offer, a bounded normalized proof
    snapshot is persisted separately so the canonical exact worker can reuse it
    while it is fresh and still revalidate item, price, seller, offer, variant,
    and marketplace shipping before promotion.

    Production catalog passes repeatedly rediscover mature verified products.
    The enqueue path therefore writes only rows that are new, materially changed,
    safely rearmed, due soon enough to consume a refreshed proof snapshot, or old
    enough to need the once-per-day retention heartbeat. Refreshing the timestamp
    of an identical fifteen-minute proof for a verified row whose next recheck is
    hours away is intentionally suppressed: that proof would expire before it
    could be consumed and the write has no correctness value.

    Verified rows use a low-write fallback cadence. A material catalog
    price/reference/selected-offer proof change therefore wakes the existing row
    to ``pending`` immediately. Cosmetic title/URL/source changes may still
    refresh stored discovery metadata but do not wake a verified row by themselves.

    A retry whose primary exact detail and exact Product Lookup both proved that
    no canonical current price was available keeps its cooldown across ordinary
    weak rediscovery. A complete fresh catalog snapshot or a material apparent
    current/reference price change still wakes that row immediately. This keeps
    Walmart's no-price population from pinning fresh-work backpressure while
    preserving fast recovery when new proof actually arrives.

    A page-retry row that receives a complete safe snapshot is explicitly
    rearmed to ``pending`` so fresh catalog proof can bypass an HTML challenge.
    The same strict proof also re-arms terminal identity rows immediately: once
    the current official catalog response contains an exact item ID, payable
    price, selected offer, seller, variant, and required marketplace shipping,
    waiting a week to retry an old missing-identity result has no safety value.
    Weak/incomplete catalog rows have an empty snapshot and cannot escape terminal
    quarantine through this path.

    Fifty-five rows use 990 parameters, below SQLite's conservative historical
    999-variable limit. A normal 75-100 row pass therefore uses no writes when
    every row is a recent unchanged rediscovery, and at most two statements
    when the complete pass is genuinely new or changed.

    Retention cleanup is intentionally not owned by discovery. The exact worker
    runs the bounded cleaner only after verified-deal fanout, so catalog enqueue
    can never put a maintenance DELETE in front of an alert that is ready to send.
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
        if previous is None or prefer_discovery_offer(
            candidate, previous, min_discount=min_discount,
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
    snapshot_refresh_cutoff = (
        now - timedelta(seconds=DISCOVERY_PROOF_REFRESH_INTERVAL_SECONDS)
    ).isoformat()
    snapshot_use_deadline = (
        now + timedelta(seconds=DISCOVERY_PROOF_MAX_AGE_SECONDS)
    ).isoformat()

    rows_to_write: list[tuple[Any, ...]] = []
    snapshot_refresh_skipped = 0
    for row in rows:
        stored = existing.get(str(row[0]))
        needs_write = _queue_row_needs_write(
            row,
            stored,
            refresh_cutoff=refresh_cutoff,
            snapshot_refresh_cutoff=snapshot_refresh_cutoff,
            snapshot_use_deadline=snapshot_use_deadline,
        )
        if needs_write:
            rows_to_write.append(row)
        elif _stale_snapshot_refresh_was_suppressed(
            row,
            stored,
            snapshot_refresh_cutoff=snapshot_refresh_cutoff,
            snapshot_use_deadline=snapshot_use_deadline,
        ):
            snapshot_refresh_skipped += 1

    safe_snapshot_rows = sum(1 for row in rows if str(row[16] or ""))
    page_retry_rearmed = sum(
        1
        for row in rows_to_write
        if str(row[16] or "")
        and existing.get(str(row[0])) is not None
        and existing[str(row[0])].status == "page_retry"
    )
    terminal_identity_rearmed = sum(
        1
        for row in rows_to_write
        if str(row[16] or "")
        and existing.get(str(row[0])) is not None
        and existing[str(row[0])].status in _TERMINAL_IDENTITY_REARM_STATUSES
    )
    verified_change_rearmed = sum(
        1
        for row in rows_to_write
        if _verified_material_change(
            row,
            existing.get(str(row[0])),
        )
    )

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
                attempt_count, next_attempt_at,
                discovery_snapshot_json, discovery_snapshot_at
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
                discovery_snapshot_json = CASE
                    WHEN excluded.discovery_snapshot_json <> ''
                    THEN excluded.discovery_snapshot_json
                    ELSE discovery_snapshot_json END,
                discovery_snapshot_at = CASE
                    WHEN excluded.discovery_snapshot_json <> ''
                    THEN excluded.discovery_snapshot_at
                    ELSE discovery_snapshot_at END,
                status = CASE
                    WHEN status IN (
                        'page_retry',
                        'incomplete_identity',
                        'identity_mismatch'
                    ) AND excluded.discovery_snapshot_json <> ''
                    THEN 'pending'
                    WHEN status IN (
                        'verified_markdown',
                        'verified_under_threshold',
                        'verified_no_reference',
                        'verified_reference',
                        'not_buyable'
                    ) AND (
                        (
                            excluded.apparent_current_cents IS NOT NULL
                            AND excluded.apparent_current_cents IS NOT apparent_current_cents
                        )
                        OR (
                            excluded.apparent_reference_cents IS NOT NULL
                            AND excluded.apparent_reference_cents IS NOT apparent_reference_cents
                        )
                        OR (
                            excluded.discovery_snapshot_json <> ''
                            AND excluded.discovery_snapshot_json
                                <> COALESCE(discovery_snapshot_json, '')
                        )
                    )
                    THEN 'pending'
                    ELSE status END,
                next_attempt_at = CASE
                    WHEN status = 'retry'
                         AND instr(lower(last_error), 'retry_kind=api_price_missing') > 0
                         AND instr(lower(last_error), 'recovery_kind=api_price_missing') > 0
                         AND excluded.discovery_snapshot_json = ''
                         AND (
                             excluded.apparent_current_cents IS NULL
                             OR excluded.apparent_current_cents IS apparent_current_cents
                         )
                         AND (
                             excluded.apparent_reference_cents IS NULL
                             OR excluded.apparent_reference_cents IS apparent_reference_cents
                         )
                    THEN next_attempt_at
                    WHEN status IN ('pending', 'retry', 'failed')
                    THEN MIN(next_attempt_at, excluded.next_attempt_at)
                    WHEN status IN (
                        'page_retry',
                        'incomplete_identity',
                        'identity_mismatch'
                    ) AND excluded.discovery_snapshot_json <> ''
                    THEN MIN(next_attempt_at, excluded.next_attempt_at)
                    WHEN status IN (
                        'verified_markdown',
                        'verified_under_threshold',
                        'verified_no_reference',
                        'verified_reference',
                        'not_buyable'
                    ) AND (
                        (
                            excluded.apparent_current_cents IS NOT NULL
                            AND excluded.apparent_current_cents IS NOT apparent_current_cents
                        )
                        OR (
                            excluded.apparent_reference_cents IS NOT NULL
                            AND excluded.apparent_reference_cents IS NOT apparent_reference_cents
                        )
                        OR (
                            excluded.discovery_snapshot_json <> ''
                            AND excluded.discovery_snapshot_json
                                <> COALESCE(discovery_snapshot_json, '')
                        )
                    )
                    THEN MIN(next_attempt_at, excluded.next_attempt_at)
                    ELSE next_attempt_at END
            """,
            params,
        )
        write_statements += 1

    if write_statements:
        await conn.commit()
    pressure = await load_walmart_exact_queue_pressure(db)
    return _BoundedVerificationQueueEnqueueResult(
        discovered=discovered,
        queued_unique=len(unique),
        pending_total=pressure.due_now,
        persisted_rows=len(rows_to_write),
        unchanged_rows=max(0, len(rows) - len(rows_to_write)),
        write_statements=write_statements,
        safe_snapshot_rows=safe_snapshot_rows,
        page_retry_rearmed=page_retry_rearmed,
        terminal_identity_rearmed=terminal_identity_rearmed,
        verified_change_rearmed=verified_change_rearmed,
        snapshot_refresh_skipped=snapshot_refresh_skipped,
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
                last_seen_at, status, next_attempt_at, last_error,
                discovery_snapshot_json, discovery_snapshot_at
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
                last_error=str(_row_get(row, "last_error", index=13) or ""),
                discovery_snapshot_json=str(
                    _row_get(row, "discovery_snapshot_json", index=14) or ""
                ),
                discovery_snapshot_at=str(
                    _row_get(row, "discovery_snapshot_at", index=15) or ""
                ),
            )
    return existing


def _queue_row_needs_write(
    row: tuple[Any, ...],
    existing: _ExistingQueueRow | None,
    *,
    refresh_cutoff: str,
    snapshot_refresh_cutoff: str,
    snapshot_use_deadline: str,
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
    ):
        if incoming and incoming != stored:
            return True

    # Catalog routes rotate constantly and are discovery telemetry only. A route
    # fills an empty hint, but a different nonempty route must not by itself
    # rewrite an otherwise identical queue row every minute.
    incoming_route = str(row[8] or "")
    if incoming_route and not existing.route_hint:
        return True

    if str(row[9] or "") != existing.source_label:
        return True
    if not existing.last_seen_at or existing.last_seen_at <= refresh_cutoff:
        return True

    incoming_snapshot = str(row[16] or "")
    if incoming_snapshot:
        if incoming_snapshot != existing.discovery_snapshot_json:
            return True

        # Any complete fresh catalog snapshot can rescue page recovery or an old
        # terminal identity result. This is a semantic status change, not a
        # heartbeat, and remains immediate even while HTML is circuit-blocked.
        if existing.status == "page_retry" or existing.status in _TERMINAL_IDENTITY_REARM_STATUSES:
            return True

        snapshot_stale = (
            not existing.discovery_snapshot_at
            or existing.discovery_snapshot_at <= snapshot_refresh_cutoff
        )
        if snapshot_stale and _snapshot_refresh_can_be_consumed(
            existing,
            snapshot_use_deadline=snapshot_use_deadline,
        ):
            return True

    if (
        existing.status in _REARM_STATUSES
        and (
            not existing.next_attempt_at
            or existing.next_attempt_at > str(row[15] or "")
        )
    ):
        if _confirmed_no_price_retry(existing) and not _catalog_retry_proof_changed(
            row,
            existing,
        ):
            return False
        return True
    return False


def _confirmed_no_price_retry(existing: _ExistingQueueRow) -> bool:
    return existing.status == "retry" and _is_confirmed_api_price_missing(
        existing.last_error
    )


def _catalog_retry_proof_changed(
    row: tuple[Any, ...],
    existing: _ExistingQueueRow,
) -> bool:
    if str(row[16] or ""):
        return True
    if row[2] is not None and int(row[2]) != existing.apparent_current_cents:
        return True
    if row[3] is not None and int(row[3]) != existing.apparent_reference_cents:
        return True
    return False


def _verified_material_change(
    row: tuple[Any, ...],
    existing: _ExistingQueueRow | None,
) -> bool:
    """Return whether discovery changed proof that a verified row must recheck."""

    if existing is None or existing.status not in _VERIFIED_REARM_STATUSES:
        return False
    if row[2] is not None and int(row[2]) != existing.apparent_current_cents:
        return True
    if row[3] is not None and int(row[3]) != existing.apparent_reference_cents:
        return True
    incoming_snapshot = str(row[16] or "")
    return bool(
        incoming_snapshot
        and incoming_snapshot != existing.discovery_snapshot_json
    )


def _snapshot_refresh_can_be_consumed(
    existing: _ExistingQueueRow,
    *,
    snapshot_use_deadline: str,
) -> bool:
    """Return whether an identical refreshed proof can be used before expiry."""

    if existing.status in _REARM_STATUSES:
        return True
    if existing.status == "verifying":
        return False
    if not existing.next_attempt_at:
        return False
    return existing.next_attempt_at <= snapshot_use_deadline


def _stale_snapshot_refresh_was_suppressed(
    row: tuple[Any, ...],
    existing: _ExistingQueueRow | None,
    *,
    snapshot_refresh_cutoff: str,
    snapshot_use_deadline: str,
) -> bool:
    if existing is None:
        return False
    incoming_snapshot = str(row[16] or "")
    if not incoming_snapshot or incoming_snapshot != existing.discovery_snapshot_json:
        return False
    if existing.status == "page_retry" or existing.status in _TERMINAL_IDENTITY_REARM_STATUSES:
        return False
    if existing.discovery_snapshot_at and existing.discovery_snapshot_at > snapshot_refresh_cutoff:
        return False
    return not _snapshot_refresh_can_be_consumed(
        existing,
        snapshot_use_deadline=snapshot_use_deadline,
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
    discovery_snapshot = build_fresh_discovery_offer_snapshot(candidate)
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
        discovery_snapshot,
        now_iso if discovery_snapshot else None,
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
