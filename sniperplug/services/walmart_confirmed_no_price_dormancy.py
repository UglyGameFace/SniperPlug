from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services.walmart_exact_verification_queue import (
    CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS,
    QUEUE_TABLE,
)


log = logging.getLogger("sniperplug.autoscan")

CONFIRMED_NO_PRICE_DORMANCY_MARKER = "confirmed_no_price_dormancy_v285"
CONFIRMED_NO_PRICE_DORMANCY_BATCH_LIMIT = 200
CONFIRMED_NO_PRICE_DORMANCY_DAYS = 30
# #277 writes last_attempt_at and next_attempt_at from the same observed timestamp,
# so the untouched retry schedule is exact. Keep a tiny SQLite/julianday tolerance
# for timestamp conversion without treating later fresh/material wakes as mature.
CONFIRMED_NO_PRICE_SCHEDULE_TOLERANCE_SECONDS = 2.0
STATUS_DUE_INDEX = f"idx_{QUEUE_TABLE}_status_due"


async def park_mature_confirmed_no_price_retries(
    conn: Any,
    *,
    now: datetime | None = None,
    limit: int = CONFIRMED_NO_PRICE_DORMANCY_BATCH_LIMIT,
) -> int:
    """Move untouched mature dual-no-price retries to a low-frequency safety probe.

    A row qualifies only when both official exact price paths persisted
    ``api_price_missing`` and its due timestamp still equals the original #277
    six-hour schedule. That schedule identity is the important guard: catalog
    discovery already shortens ``next_attempt_at`` when it sees a complete fresh
    selected-offer snapshot or a material apparent current/reference-price
    change. A shortened wake therefore does not match this pass, even if it
    happens more than six hours after the previous exact attempt.

    Qualifying rows keep ``status='retry'`` and the existing ``last_error``. The
    established bulk enqueue logic can therefore continue to preserve weak
    rediscovery while waking real fresh/material evidence immediately. A 30-day
    fallback probe remains so an upstream Affiliate API recovery can eventually
    be detected even if catalog evidence itself does not materially change.
    """

    if conn is None or limit <= 0:
        return 0

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    now_iso = current.isoformat()
    dormant_until = (
        current + timedelta(days=CONFIRMED_NO_PRICE_DORMANCY_DAYS)
    ).isoformat()
    bounded_limit = min(
        CONFIRMED_NO_PRICE_DORMANCY_BATCH_LIMIT,
        max(1, int(limit)),
    )
    retry_seconds = int(CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS)
    tolerance = float(CONFIRMED_NO_PRICE_SCHEDULE_TOLERANCE_SECONDS)

    cursor = await conn.execute(
        f"""
        WITH mature_no_price AS (
            SELECT item_id
            FROM {QUEUE_TABLE} INDEXED BY {STATUS_DUE_INDEX}
            WHERE status = 'retry'
              AND next_attempt_at <= ?
              AND last_attempt_at IS NOT NULL
              AND instr(lower(last_error), 'retry_kind=api_price_missing') > 0
              AND instr(lower(last_error), 'recovery_kind=api_price_missing') > 0
              AND abs(
                    (
                        julianday(next_attempt_at)
                        - julianday(last_attempt_at, '+{retry_seconds} seconds')
                    ) * 86400.0
                  ) <= ?
              AND (lease_until IS NULL OR lease_until < ?)
            ORDER BY next_attempt_at ASC
            LIMIT ?
        )
        UPDATE {QUEUE_TABLE} AS target
        SET next_attempt_at = ?
        WHERE target.item_id IN (SELECT item_id FROM mature_no_price)
          AND target.status = 'retry'
          AND target.next_attempt_at <= ?
          AND target.last_attempt_at IS NOT NULL
          AND instr(lower(target.last_error), 'retry_kind=api_price_missing') > 0
          AND instr(lower(target.last_error), 'recovery_kind=api_price_missing') > 0
          AND abs(
                (
                    julianday(target.next_attempt_at)
                    - julianday(
                        target.last_attempt_at,
                        '+{retry_seconds} seconds'
                    )
                ) * 86400.0
              ) <= ?
          AND (target.lease_until IS NULL OR target.lease_until < ?)
        RETURNING item_id
        """,
        (
            now_iso,
            tolerance,
            now_iso,
            bounded_limit,
            dormant_until,
            now_iso,
            tolerance,
            now_iso,
        ),
    )
    parked = len(await cursor.fetchall())
    if parked:
        await conn.commit()
        log.info(
            "Walmart confirmed no-price retries parked "
            "marker=%s parked=%s limit=%s safety_reprobe_days=%s",
            CONFIRMED_NO_PRICE_DORMANCY_MARKER,
            parked,
            bounded_limit,
            CONFIRMED_NO_PRICE_DORMANCY_DAYS,
        )
    return parked


__all__ = [
    "CONFIRMED_NO_PRICE_DORMANCY_BATCH_LIMIT",
    "CONFIRMED_NO_PRICE_DORMANCY_DAYS",
    "CONFIRMED_NO_PRICE_DORMANCY_MARKER",
    "CONFIRMED_NO_PRICE_SCHEDULE_TOLERANCE_SECONDS",
    "park_mature_confirmed_no_price_retries",
]
