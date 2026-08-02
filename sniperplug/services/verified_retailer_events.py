from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.source_candidate_snapshot import (
    deserialize_source_candidate,
    serialize_source_candidate,
)


EVENT_TABLE = "retailer_verified_deal_events"
EVENT_RETENTION_DAYS = 45
EVENT_LEASE_SECONDS = 30 * 60


@dataclass(frozen=True)
class ClaimedRetailerEvent:
    event_key: str
    retailer: str
    product_key: str
    event_type: str
    source_verified_at: str
    candidate: SourceCandidate
    claim_token: str


async def ensure_verified_retailer_event_table(db: Any) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {EVENT_TABLE} (
            event_key TEXT PRIMARY KEY,
            retailer TEXT NOT NULL,
            product_key TEXT NOT NULL,
            event_type TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            source_verified_at TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_attempt_at TEXT,
            processed_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            claim_token TEXT NOT NULL DEFAULT '',
            lease_until TEXT
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{EVENT_TABLE}_pending "
        f"ON {EVENT_TABLE} (processed_at, lease_until, first_seen_at)"
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{EVENT_TABLE}_retailer "
        f"ON {EVENT_TABLE} (retailer, source_verified_at)"
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=EVENT_RETENTION_DAYS)).isoformat()
    await conn.execute(
        f"DELETE FROM {EVENT_TABLE} WHERE processed_at IS NOT NULL AND first_seen_at < ?",
        (cutoff,),
    )
    await conn.commit()


async def publish_verified_retailer_event(
    db: Any,
    *,
    event_key: str,
    retailer: str,
    product_key: str,
    event_type: str,
    candidate: SourceCandidate,
    source_verified_at: str,
) -> bool:
    await ensure_verified_retailer_event_table(db)
    clean_event_key = str(event_key or "").strip()
    clean_retailer = str(retailer or "").strip().lower()
    clean_product_key = str(product_key or "").strip()
    clean_event_type = str(event_type or "").strip().lower()
    if not all((clean_event_key, clean_retailer, clean_product_key, clean_event_type)):
        raise ValueError("verified retailer event identity is incomplete")

    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT 1 FROM {EVENT_TABLE} WHERE event_key = ? LIMIT 1",
        (clean_event_key,),
    )
    if await cursor.fetchone() is not None:
        return False

    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        INSERT INTO {EVENT_TABLE} (
            event_key, retailer, product_key, event_type, snapshot_json,
            source_verified_at, first_seen_at, attempt_count, last_error,
            claim_token
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, '', '')
        ON CONFLICT(event_key) DO NOTHING
        """,
        (
            clean_event_key,
            clean_retailer,
            clean_product_key,
            clean_event_type,
            serialize_source_candidate(candidate),
            source_verified_at,
            now,
        ),
    )
    await conn.commit()
    verify = await conn.execute(
        f"SELECT retailer, product_key, event_type FROM {EVENT_TABLE} WHERE event_key = ?",
        (clean_event_key,),
    )
    row = await verify.fetchone()
    return bool(
        row is not None
        and str(_row_get(row, "retailer", 0) or "") == clean_retailer
        and str(_row_get(row, "product_key", 1) or "") == clean_product_key
        and str(_row_get(row, "event_type", 2) or "") == clean_event_type
    )


async def claim_verified_retailer_events(
    db: Any,
    *,
    limit: int = 20,
) -> list[ClaimedRetailerEvent]:
    await ensure_verified_retailer_event_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cursor = await conn.execute(
        f"""
        SELECT event_key, retailer, product_key, event_type,
               source_verified_at, snapshot_json
        FROM {EVENT_TABLE}
        WHERE processed_at IS NULL
          AND (lease_until IS NULL OR lease_until <= ?)
        ORDER BY first_seen_at ASC
        LIMIT ?
        """,
        (now_iso, max(1, int(limit))),
    )
    rows = await cursor.fetchall()
    lease_until = (now + timedelta(seconds=EVENT_LEASE_SECONDS)).isoformat()
    claimed: list[ClaimedRetailerEvent] = []

    for row in rows:
        event_key = str(_row_get(row, "event_key", 0) or "")
        if not event_key:
            continue
        token = uuid.uuid4().hex
        await conn.execute(
            f"""
            UPDATE {EVENT_TABLE}
            SET claim_token = ?, lease_until = ?, last_attempt_at = ?,
                attempt_count = attempt_count + 1
            WHERE event_key = ?
              AND processed_at IS NULL
              AND (lease_until IS NULL OR lease_until <= ?)
            """,
            (token, lease_until, now_iso, event_key, now_iso),
        )
        verify = await conn.execute(
            f"SELECT claim_token FROM {EVENT_TABLE} WHERE event_key = ?",
            (event_key,),
        )
        verify_row = await verify.fetchone()
        if str(_row_get(verify_row, "claim_token", 0) or "") != token:
            continue

        candidate = deserialize_source_candidate(_row_get(row, "snapshot_json", 5))
        if candidate is None:
            await mark_verified_retailer_event_processed(
                db,
                event_key=event_key,
                claim_token=token,
                note="malformed candidate snapshot discarded safely",
            )
            continue
        claimed.append(
            ClaimedRetailerEvent(
                event_key=event_key,
                retailer=str(_row_get(row, "retailer", 1) or ""),
                product_key=str(_row_get(row, "product_key", 2) or ""),
                event_type=str(_row_get(row, "event_type", 3) or ""),
                source_verified_at=str(_row_get(row, "source_verified_at", 4) or ""),
                candidate=candidate,
                claim_token=token,
            )
        )
    await conn.commit()
    return claimed


async def mark_verified_retailer_event_processed(
    db: Any,
    *,
    event_key: str,
    claim_token: str,
    note: str = "",
) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        UPDATE {EVENT_TABLE}
        SET processed_at = ?, last_error = ?, claim_token = '', lease_until = NULL
        WHERE event_key = ? AND claim_token = ?
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            _compact(note, 1000),
            event_key,
            claim_token,
        ),
    )
    await conn.commit()


async def release_verified_retailer_event(
    db: Any,
    *,
    event_key: str,
    claim_token: str,
    error: str,
) -> None:
    conn = db.require_conn()
    await conn.execute(
        f"""
        UPDATE {EVENT_TABLE}
        SET last_error = ?, claim_token = '', lease_until = NULL
        WHERE event_key = ? AND claim_token = ?
        """,
        (_compact(error, 1000), event_key, claim_token),
    )
    await conn.commit()


def _row_get(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _compact(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[: max(0, int(limit))]
