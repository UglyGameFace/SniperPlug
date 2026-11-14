"""Read-only, opt-in SniperPlug operating metrics for the owner website.

This reads existing local SQLite tables via SELECT and periodically emits a
small, signed, sanitized snapshot. It never changes the bot database, deal
selection, rate budget, Discord sends or worker scheduling.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from typing import Any

import aiohttp

LOG = logging.getLogger("sniperplug.ops")
ENDPOINT = "https://sniperplug.com/api/sniperplug-ops/ingest"
DEFAULT_INTERVAL_SECONDS = 300
TABLES = {
    "walmart_exact_detail_queue",
    "walmart_worker_cycle_health",
    "walmart_global_catalog_autoscan_state",
    "walmart_global_exact_deal_events",
    "guild_public_deal_posts",
}
ERROR_CLASSES = (
    ("429", "rate_limited"),
    ("rate limit", "rate_limited"),
    ("403", "access_denied"),
    ("timeout", "timeout"),
    ("timed out", "timeout"),
    ("identity", "identity_incomplete"),
    ("seller", "seller_proof"),
    ("offer", "offer_proof"),
    ("no price", "price_missing"),
    ("price missing", "price_missing"),
    ("unavailable", "availability"),
    ("locked", "database_busy"),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def error_class(value: Any) -> str:
    text = str(value or "").lower()
    for term, label in ERROR_CLASSES:
        if term in text:
            return label
    return "other_error" if text else ""


def value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        pass
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def safe_count(x: Any) -> int:
    try:
        return max(0, min(int(x), 10**10))
    except (ValueError, TypeError, OverflowError):
        return 0


def safe_cents(x: Any) -> int | None:
    try:
        n = int(x)
        return n if 0 < n <= 10**10 else None
    except (ValueError, TypeError, OverflowError):
        return None


def clean_text(x: Any, maximum: int = 100) -> str:
    return " ".join(str(x or "").split())[:maximum]


def safe_walmart_url(item_id: Any) -> str | None:
    item = str(item_id or "")
    return f"https://www.walmart.com/ip/{item}" if re.fullmatch(r"\d{5,20}", item) else None


async def query_rows(conn: Any, sql: str, params: tuple = ()) -> list[Any]:
    cursor = await conn.execute(sql, params)
    return list(await cursor.fetchall())


async def read_snapshot(db: Any, *, now: datetime | None = None) -> dict:
    """Bounded SELECT-only collection. Missing tables are labeled, not created."""
    now = now or utcnow()
    cutoff = (now - timedelta(hours=24)).isoformat()
    iso_now = now.isoformat()
    conn = db.require_conn()
    existing = await query_rows(
        conn, "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (" +
        ", ".join("?" for _ in TABLES) + ")", tuple(sorted(TABLES))
    )
    names = {str(value(row, "name", 0) or "") for row in existing}
    snapshot: dict[str, Any] = {
        "queue": None, "posts": None, "events": None, "catalog": None,
        "workers": {"catalog": None, "exact": None}, "deals": [],
        "missing_tables": sorted(TABLES - names),
    }
    if "walmart_exact_detail_queue" in names:
        queue = await query_rows(conn,
            "SELECT status, COUNT(*) AS count FROM walmart_exact_detail_queue GROUP BY status")
        counts = {str(value(row, "status", 0)): safe_count(value(row, "count", 1)) for row in queue}
        due_rows = await query_rows(conn,
            """SELECT COUNT(*) FROM walmart_exact_detail_queue
               WHERE status IN ('pending', 'retry', 'failed', 'page_retry', 'verifying')
                 AND next_attempt_at <= ?
                 AND (lease_until IS NULL OR lease_until < ?)""", (iso_now, iso_now))
        last_verified = await query_rows(conn,
            "SELECT MAX(verified_at) FROM walmart_exact_detail_queue WHERE verified_at IS NOT NULL")
        snapshot["queue"] = {
            "total": sum(counts.values()),
            "due": safe_count(value(due_rows[0], "COUNT(*)", 0)) if due_rows else 0,
            "verified": sum(n for s, n in counts.items() if s.startswith("verified_")),
            "pending": counts.get("pending", 0), "retry": counts.get("retry", 0),
            "failed": counts.get("failed", 0), "page_retry": counts.get("page_retry", 0),
            "identity_blocked": counts.get("incomplete_identity", 0) + counts.get("identity_mismatch", 0),
            "last_verified_at": value(last_verified[0], "MAX(verified_at)", 0) if last_verified else None,
        }
        columns = ("item_id", "title", "status", "route_hint", "first_seen_at",
                   "last_seen_at", "verified_at", "last_attempt_at", "last_error",
                   "exact_current_cents", "exact_reference_cents", "apparent_current_cents")
        sql_fields = ", ".join(columns)
        verified_rows = await query_rows(conn,
            f"""SELECT {sql_fields} FROM walmart_exact_detail_queue
                WHERE verified_at >= ? ORDER BY verified_at DESC LIMIT 24""", (cutoff,))
        failed_rows = await query_rows(conn,
            f"""SELECT {sql_fields} FROM walmart_exact_detail_queue
                WHERE status IN ('retry', 'failed', 'page_retry', 'identity_mismatch', 'incomplete_identity')
                  AND last_seen_at >= ?
                ORDER BY last_seen_at DESC LIMIT 12""", (cutoff,))
        seen = set()
        for row in [*verified_rows, *failed_rows]:
            entry = {key: value(row, key, index) for index, key in enumerate(columns)}
            item_id = str(entry.get("item_id") or "")
            if item_id in seen or not re.fullmatch(r"\d{5,20}", item_id):
                continue
            seen.add(item_id)
            snapshot["deals"].append({
                "item_id": item_id, "title": clean_text(entry["title"], 180),
                "retailer": "walmart",
                "route": clean_text(entry["route_hint"], 70) or "unknown",
                "status": clean_text(entry["status"], 64),
                "product_url": safe_walmart_url(item_id),
                "exact_current_cents": safe_cents(entry["exact_current_cents"]),
                "exact_reference_cents": safe_cents(entry["exact_reference_cents"]),
                "apparent_current_cents": safe_cents(entry["apparent_current_cents"]),
                "first_seen_at": entry["first_seen_at"],
                "last_seen_at": entry["last_seen_at"],
                "verified_at": entry["verified_at"],
                "last_attempt_at": entry["last_attempt_at"],
                "error_class": error_class(entry["last_error"]),
            })
        snapshot["deals"] = snapshot["deals"][:36]
    if "guild_public_deal_posts" in names:
        rows = await query_rows(conn, """
            SELECT COUNT(*) AS total, MAX(posted_at) AS latest
            FROM guild_public_deal_posts WHERE status = 'posted' AND posted_at >= ?
        """, (cutoff,))
        reserved = await query_rows(conn, """
            SELECT COUNT(*) FROM guild_public_deal_posts
            WHERE status = 'reserved' AND first_seen_at >= ?
        """, (cutoff,))
        snapshot["posts"] = {
            "posts_24h": safe_count(value(rows[0], "total", 0)) if rows else 0,
            "latest_post_at": value(rows[0], "latest", 1) if rows else None,
            "reserved_24h": safe_count(value(reserved[0], "COUNT(*)", 0)) if reserved else 0,
        }
    if "walmart_global_exact_deal_events" in names:
        events = await query_rows(conn, """
            SELECT COUNT(*) AS count, MAX(first_seen_at) AS latest,
              SUM(CASE WHEN processed_at IS NOT NULL THEN 1 ELSE 0 END) AS processed,
              SUM(CASE WHEN last_error != '' THEN 1 ELSE 0 END) AS errors
            FROM walmart_global_exact_deal_events WHERE first_seen_at >= ?
        """, (cutoff,))
        row = events[0] if events else None
        snapshot["events"] = {
            "events_24h": safe_count(value(row, "count", 0)),
            "processed_24h": safe_count(value(row, "processed", 2)),
            "event_errors_24h": safe_count(value(row, "errors", 3)),
            "latest_event_at": value(row, "latest", 1),
        }
    if "walmart_global_catalog_autoscan_state" in names:
        rows = await query_rows(conn, """
            SELECT completed_routes, completed_passes, last_started_at, last_completed_at,
                   CASE WHEN lease_until > ? THEN 1 ELSE 0 END AS active
            FROM walmart_global_catalog_autoscan_state WHERE state_key = 'walmart'
            LIMIT 1
        """, (iso_now,))
        if rows:
            row = rows[0]
            snapshot["catalog"] = {
                "completed_routes": safe_count(value(row, "completed_routes", 0)),
                "completed_passes": safe_count(value(row, "completed_passes", 1)),
                "last_started_at": value(row, "last_started_at", 2),
                "last_completed_at": value(row, "last_completed_at", 3),
                "claim_active": bool(value(row, "active", 4)),
            }
    if "walmart_worker_cycle_health" in names:
        rows = await query_rows(conn, """
            SELECT worker, total_cycles, total_failures, total_timeouts,
              consecutive_failures, last_batch_claimed, last_api_price_missing,
              last_cycle_at, last_success_at, last_failure_at
            FROM walmart_worker_cycle_health
            WHERE worker IN ('catalog', 'exact')
        """)
        keys = (
            "worker", "total_cycles", "total_failures", "total_timeouts",
            "consecutive_failures", "last_batch_claimed", "last_api_price_missing",
            "last_cycle_at", "last_success_at", "last_failure_at",
        )
        for row in rows:
            w = {name: value(row, name, index) for index, name in enumerate(keys)}
            worker_name = str(w.pop("worker") or "")
            if worker_name not in ("catalog", "exact"):
                continue
            snapshot["workers"][worker_name] = {
                name: safe_count(w.get(name)) if name in (
                    "total_cycles", "total_failures", "total_timeouts",
                    "consecutive_failures", "last_batch_claimed", "last_api_price_missing",
                ) else w.get(name) for name in w
            }
    return snapshot


def sign_payload(secret: str, timestamp: int, raw: bytes) -> str:
    return hmac.new(
        secret.encode("utf8"), str(timestamp).encode("ascii") + b"." + raw, hashlib.sha256
    ).hexdigest()


async def publish_once(bot: Any, *, session: aiohttp.ClientSession | None = None) -> bool:
    secret = os.getenv("SNIPERPLUG_OPS_INGEST_SECRET", "").strip()
    if len(secret) < 32:
        return False
    # No collector may send from an unready bot. Incomplete startup is unknown.
    data = await read_snapshot(bot.db)
    release = os.getenv("SNIPERPLUG_RELEASE_SHA", "").strip()
    result = {
        "schema": 1,
        "source": "discloud-sniperplug",
        "id": str(uuid.uuid4()),
        "observed_at": utcnow().isoformat(),
        "bot": {
            "ready": bool(bot.is_ready()),
            "guild_count": len(bot.guilds),
            "backend": str(getattr(bot.db, "backend", "unknown")),
            "uptime_seconds": int(time.monotonic() - getattr(bot, "_ops_started_monotonic", time.monotonic())),
            "release_sha": release if re.fullmatch(r"[a-f0-9]{40}", release, re.IGNORECASE) else None,
        },
        "metrics": {
            "queue": data["queue"], "posts": data["posts"], "events": data["events"],
            "catalog": data["catalog"], "workers": data["workers"],
        },
        "deals": data["deals"],
    }
    raw = json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf8")
    if len(raw) > 64_000:
        LOG.warning("Ops snapshot exceeds size cap; not transmitted")
        return False
    timestamp = int(time.time())
    headers = {
        "content-type": "application/json",
        "x-sniperplug-timestamp": str(timestamp),
        "x-sniperplug-signature": "sha256=" + sign_payload(secret, timestamp, raw),
    }
    if session is not None:
        async with session.post(ENDPOINT, data=raw, headers=headers, allow_redirects=False) as response:
            return response.status == 202
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        async with client.post(ENDPOINT, data=raw, headers=headers, allow_redirects=False) as response:
            return response.status == 202


async def publish_loop(bot: Any, *, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
    """Optional observation loop; only a configured secret enables it."""
    if len(os.getenv("SNIPERPLUG_OPS_INGEST_SECRET", "").strip()) < 32:
        LOG.info("Operational telemetry disabled: no shared ingest secret configured")
        return
    await bot.wait_until_ready()
    LOG.info("Operational telemetry enabled: SELECT-only snapshot every %ss", interval_seconds)
    while not bot.is_closed():
        try:
            success = await asyncio.wait_for(publish_once(bot), timeout=15)
            if not success:
                LOG.warning("Operational telemetry delivery rejected or unavailable; bot scanning continues")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("Operational telemetry unavailable (%s); bot unaffected", type(exc).__name__)
        await asyncio.sleep(max(60, interval_seconds))
