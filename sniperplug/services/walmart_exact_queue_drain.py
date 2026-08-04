from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_price_enrichment import (
    _percent_off,
    _positive_number,
    _trusted_reference,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_LEASE_SECONDS,
    QUEUE_RETENTION_DAYS,
    QUEUE_TABLE,
    _QueueClaim,
    _candidate_snapshot,
    _classify_exact_candidate,
    _compact_text,
    _int_or_none,
    _price_to_cents,
    _row_get,
)
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity


TERMINAL_IDENTITY_STATUSES = ("incomplete_identity", "identity_mismatch")
PUBLIC_ALERT_RECHECK_PERCENT = 50.0
MID_TIER_RECHECK_PERCENT = 30.0


async def claim_due_rows_batched(
    conn: Any,
    *,
    now: datetime,
    limit: int,
) -> list[_QueueClaim]:
    """Select an ordered slice, then lease only those rows with one small update.

    The former MATERIALIZED CTE plus UPDATE ... RETURNING scanned and rewrote the
    remote Turso queue in one native libSQL call. With roughly 12k rows and a
    large scheduled-recheck backlog, that statement took 48-66 seconds and
    starved Discord's heartbeat even though it ran through ``asyncio.to_thread``.

    Two bounded statements are intentionally safer here:

    1. an indexed SELECT returns at most ``limit`` candidate snapshots;
    2. a guarded UPDATE leases only those item IDs and RETURNING confirms which
       rows this process actually acquired.

    A shared lease token still protects all final writes. If another process
    races between the two statements, the UPDATE guards and returned IDs prevent
    this worker from processing rows it did not lease.
    """

    now_utc = now.astimezone(timezone.utc)
    now_iso = now_utc.isoformat()
    discovery_cutoff = (
        now_utc - timedelta(days=QUEUE_RETENTION_DAYS)
    ).isoformat()
    bounded_limit = max(1, int(limit))
    batch_token = uuid.uuid4().hex
    lease_until = (
        now_utc + timedelta(seconds=QUEUE_LEASE_SECONDS)
    ).isoformat()

    select_cursor = await conn.execute(
        f"""
        SELECT
            item_id, title, product_url, image_url,
            apparent_current_cents, apparent_reference_cents,
            route_hint, attempt_count
        FROM {QUEUE_TABLE}
        WHERE next_attempt_at <= ?
          AND last_seen_at >= ?
          AND status NOT IN ('incomplete_identity', 'identity_mismatch')
          AND (lease_until IS NULL OR lease_until < ?)
        ORDER BY
            CASE status
                WHEN 'pending' THEN 0
                WHEN 'retry' THEN 1
                WHEN 'failed' THEN 2
                WHEN 'verifying' THEN 3
                WHEN 'verified_markdown' THEN 4
                WHEN 'verified_under_threshold' THEN 5
                WHEN 'verified_no_reference' THEN 6
                WHEN 'verified_reference' THEN 7
                WHEN 'not_buyable' THEN 8
                ELSE 9
            END,
            priority_score DESC,
            last_seen_at DESC
        LIMIT ?
        """,
        (now_iso, discovery_cutoff, now_iso, bounded_limit),
    )
    selected_rows = await select_cursor.fetchall()
    if not selected_rows:
        return []

    selected_by_id: dict[str, Any] = {}
    selected_order: list[str] = []
    for row in selected_rows:
        item_id = str(_row_get(row, "item_id", index=0) or "").strip()
        if not item_id or item_id in selected_by_id:
            continue
        selected_by_id[item_id] = row
        selected_order.append(item_id)

    if not selected_order:
        return []

    placeholders = ", ".join("?" for _ in selected_order)
    update_cursor = await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET lease_token = ?, lease_until = ?, status = 'verifying'
        WHERE item_id IN ({placeholders})
          AND next_attempt_at <= ?
          AND last_seen_at >= ?
          AND status NOT IN ('incomplete_identity', 'identity_mismatch')
          AND (lease_until IS NULL OR lease_until < ?)
        RETURNING item_id
        """,
        (
            batch_token,
            lease_until,
            *selected_order,
            now_iso,
            discovery_cutoff,
            now_iso,
        ),
    )
    leased_rows = await update_cursor.fetchall()
    await conn.commit()

    leased_ids = {
        str(_row_get(row, "item_id", index=0) or "").strip()
        for row in leased_rows
    }
    claims: list[_QueueClaim] = []
    for item_id in selected_order:
        if item_id not in leased_ids:
            continue
        row = selected_by_id[item_id]
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
                lease_token=batch_token,
                attempt_count=int(_row_get(row, "attempt_count", index=7) or 0),
            )
        )
    return claims


async def store_exact_candidate_with_tiered_recheck(
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
    next_attempt = (now + tiered_recheck_delay(status, discount)).isoformat()
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


def tiered_recheck_delay(status: str, discount_percent: float) -> timedelta:
    """Keep strong deals fresh without creating an impossible hourly queue."""

    discount = max(0.0, float(discount_percent or 0.0))
    if status == "verified_markdown":
        if discount >= PUBLIC_ALERT_RECHECK_PERCENT:
            return timedelta(hours=4)
        if discount >= MID_TIER_RECHECK_PERCENT:
            return timedelta(hours=12)
        return timedelta(hours=24)
    if status == "verified_no_reference":
        return timedelta(hours=24)
    if status == "verified_under_threshold":
        return timedelta(hours=24)
    if status == "not_buyable":
        return timedelta(hours=24)
    return timedelta(hours=12)
