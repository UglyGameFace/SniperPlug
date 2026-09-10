from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_verification_queue import (
    CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS,
    QUEUE_TABLE,
)


LEGACY_COOLDOWN_BATCH_LIMIT = 250


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def normalize_legacy_confirmed_no_price_cooldowns(
    db: Any,
    *,
    now: datetime | None = None,
    limit: int = LEGACY_COOLDOWN_BATCH_LIMIT,
) -> int:
    """Lift unambiguously stale pre-#277 no-price schedules to the six-hour floor.

    PR #277 applies the six-hour delay when a dual ``api_price_missing`` failure
    is persisted and protects that delay from ordinary rediscovery. Rows written
    before #277 can still carry the same proven failure signature with an older
    five/ten/twenty-minute exponential schedule. Those legacy timestamps can
    mature in waves after deployment and keep fresh/retry pressure hot even
    though newly written failures are already using the correct cooldown.

    This compatibility pass is deliberately conservative and bounded. It only
    touches retry rows whose persisted primary and recovery kinds both prove
    ``api_price_missing``, whose six-hour floor has not yet elapsed, and whose
    catalog ``last_seen_at`` is not newer than the confirming exact attempt.
    Therefore a post-attempt rediscovery, including a material catalog change or
    fresh proof arrival, is never delayed. Active leases are also excluded.

    The target is exactly ``last_attempt_at + 6h`` rather than ``now + 6h`` so
    deployment never restarts the cooldown clock. At most ``limit`` rows are
    changed in one bulk statement.
    """

    if db is None or limit <= 0:
        return 0

    conn = db.require_conn()
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_iso = current.isoformat()
    bounded_limit = max(1, int(limit))
    cursor = await conn.execute(
        f"""
        SELECT item_id, last_attempt_at, next_attempt_at
        FROM {QUEUE_TABLE}
        WHERE status = 'retry'
          AND instr(lower(last_error), 'retry_kind=api_price_missing') > 0
          AND instr(lower(last_error), 'recovery_kind=api_price_missing') > 0
          AND last_attempt_at IS NOT NULL
          AND julianday(last_seen_at) <= julianday(last_attempt_at)
          AND julianday(next_attempt_at)
                < julianday(last_attempt_at, '+6 hours')
          AND julianday(last_attempt_at, '+6 hours') > julianday(?)
          AND (lease_until IS NULL OR lease_until < ?)
        ORDER BY next_attempt_at ASC
        LIMIT ?
        """,
        (now_iso, now_iso, bounded_limit),
    )
    selected = list(await cursor.fetchall())
    if not selected:
        return 0

    updates: list[tuple[str, str, str, str]] = []
    for row in selected:
        item_id = str(row[0] or "").strip()
        last_attempt_raw = str(row[1] or "").strip()
        next_attempt_raw = str(row[2] or "").strip()
        last_attempt = _parse_timestamp(last_attempt_raw)
        if not item_id or last_attempt is None or not next_attempt_raw:
            continue
        target = last_attempt + timedelta(
            seconds=CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS
        )
        if target <= current:
            continue
        updates.append(
            (
                item_id,
                last_attempt_raw,
                next_attempt_raw,
                target.isoformat(),
            )
        )

    if not updates:
        return 0

    placeholders = ",".join("(?, ?, ?, ?)" for _ in updates)
    params = tuple(value for update in updates for value in update)
    update_cursor = await conn.execute(
        f"""
        WITH cooldowns(
            item_id,
            expected_last_attempt_at,
            expected_next_attempt_at,
            target_next_attempt_at
        ) AS (
            VALUES {placeholders}
        )
        UPDATE {QUEUE_TABLE} AS target
        SET next_attempt_at = cooldowns.target_next_attempt_at
        FROM cooldowns
        WHERE target.item_id = cooldowns.item_id
          AND target.status = 'retry'
          AND target.last_attempt_at = cooldowns.expected_last_attempt_at
          AND target.next_attempt_at = cooldowns.expected_next_attempt_at
          AND instr(lower(target.last_error), 'retry_kind=api_price_missing') > 0
          AND instr(lower(target.last_error), 'recovery_kind=api_price_missing') > 0
          AND julianday(target.last_seen_at) <= julianday(target.last_attempt_at)
          AND (target.lease_until IS NULL OR target.lease_until < ?)
        RETURNING item_id
        """,
        (*params, now_iso),
    )
    return len(await update_cursor.fetchall())
