from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


async def ensure_autoscan_history_table(db) -> None:
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_auto_scan_report_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            scan_key TEXT NOT NULL,
            ran_at TEXT NOT NULL,
            allowed INTEGER NOT NULL DEFAULT 0,
            category TEXT,
            threshold INTEGER DEFAULT 0,
            confidence_floor INTEGER DEFAULT 0,
            checked INTEGER DEFAULT 0,
            searches INTEGER DEFAULT 0,
            total_cards INTEGER DEFAULT 0,
            confidence_ready INTEGER DEFAULT 0,
            public_attempt INTEGER DEFAULT 0,
            posted INTEGER DEFAULT 0,
            dupes INTEGER DEFAULT 0,
            not_alertable INTEGER DEFAULT 0,
            disabled INTEGER DEFAULT 0,
            payload_json TEXT NOT NULL
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_scan_report_latest ON guild_auto_scan_report_history (guild_id, retailer, scan_key, ran_at)")
    await conn.commit()


async def save_autoscan_report(db, *, guild_id: int, retailer: str, scan_key: str, payload: dict[str, Any]) -> None:
    await ensure_autoscan_history_table(db)
    conn = db.require_conn()
    ran_at = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        """
        INSERT INTO guild_auto_scan_report_history (
            guild_id, retailer, scan_key, ran_at, allowed, category, threshold, confidence_floor,
            checked, searches, total_cards, confidence_ready, public_attempt, posted, dupes,
            not_alertable, disabled, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            retailer,
            scan_key,
            ran_at,
            int(bool(payload.get("allowed"))),
            str(payload.get("category_label") or payload.get("category") or ""),
            int(payload.get("threshold") or 0),
            int(payload.get("confidence_floor") or 0),
            int(payload.get("checked") or 0),
            int(payload.get("searches") or 0),
            int(payload.get("total_cards") or 0),
            parse_confidence_ready(payload.get("confidence_summary")),
            int(payload.get("public_attempt") or 0),
            int(payload.get("posted") or 0),
            int(payload.get("dupes") or 0),
            int(payload.get("not_alertable") or 0),
            int(payload.get("disabled") or 0),
            json.dumps(payload, sort_keys=True, default=str),
        ),
    )
    await conn.execute(
        """
        DELETE FROM guild_auto_scan_report_history
        WHERE guild_id = ? AND retailer = ? AND scan_key = ?
          AND id NOT IN (
              SELECT id FROM guild_auto_scan_report_history
              WHERE guild_id = ? AND retailer = ? AND scan_key = ?
              ORDER BY ran_at DESC
              LIMIT 50
          )
        """,
        (guild_id, retailer, scan_key, guild_id, retailer, scan_key),
    )
    await conn.commit()


async def latest_autoscan_report(db, *, guild_id: int, retailer: str, scan_key: str) -> dict[str, Any] | None:
    try:
        await ensure_autoscan_history_table(db)
        conn = db.require_conn()
        cursor = await conn.execute(
            """
            SELECT ran_at, payload_json
            FROM guild_auto_scan_report_history
            WHERE guild_id = ? AND retailer = ? AND scan_key = ?
            ORDER BY ran_at DESC
            LIMIT 1
            """,
            (guild_id, retailer, scan_key),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        payload["saved_at"] = str(row["ran_at"])
        return payload
    except Exception:
        return None


async def recent_autoscan_post_count(db, *, guild_id: int, retailer: str, scan_key: str, limit: int = 20) -> int:
    try:
        await ensure_autoscan_history_table(db)
        conn = db.require_conn()
        cursor = await conn.execute(
            """
            SELECT SUM(posted) AS posted
            FROM (
                SELECT posted
                FROM guild_auto_scan_report_history
                WHERE guild_id = ? AND retailer = ? AND scan_key = ?
                ORDER BY ran_at DESC
                LIMIT ?
            )
            """,
            (guild_id, retailer, scan_key, max(1, int(limit))),
        )
        row = await cursor.fetchone()
        return int(row["posted"] if row and row["posted"] is not None else 0)
    except Exception:
        return 0


def parse_confidence_ready(value: Any) -> int:
    text = str(value or "")
    marker = "confidence-ready: **"
    if marker not in text:
        return 0
    try:
        tail = text.split(marker, 1)[1]
        return int(tail.split("**", 1)[0].strip())
    except Exception:
        return 0


def format_latest_report_line(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "No detailed auto-scan report saved yet. Wait for the next scheduled run or use `/autoscan_now`."

    parts = [
        f"Saved: `{payload.get('saved_at', 'unknown')}`",
        f"Category: **{payload.get('category_label') or payload.get('category') or 'unknown'}** • Threshold: **{payload.get('threshold', 0)}%** • Confidence floor: **{payload.get('confidence_floor', 0)}/100**",
        f"Checked: **{payload.get('checked', 0)}** products / **{payload.get('searches', 0)}** searches • Verified: **{payload.get('total_cards', 0)}**",
        f"Confidence: {payload.get('confidence_summary') or 'n/a'}",
        f"Fresh filter: {payload.get('repeat_summary') or 'n/a'}",
        f"Public guard: posted **{payload.get('posted', 0)}** • dupes **{payload.get('dupes', 0)}** • not alertable **{payload.get('not_alertable', 0)}** • disabled **{payload.get('disabled', 0)}**",
    ]

    errors = payload.get("errors") or ()
    warnings = payload.get("warnings") or ()
    verification = payload.get("verification_failure_summary") or ""
    review = payload.get("review_candidate_summary") or ""
    decision = payload.get("decision_trail_summary") or ""
    routes = payload.get("route_summary") or ""

    if errors:
        parts.append("Errors: " + "; ".join(str(error) for error in list(errors)[:3]))
    if warnings:
        parts.append("Warnings: " + "; ".join(str(warning) for warning in list(warnings)[:3]))
    if verification:
        parts.append("Verification blockers: " + str(verification))
    if review:
        parts.append("Review/scout audit: " + str(review))
    if decision:
        parts.append("Candidate decision trail: " + str(decision))
    if routes:
        parts.append("Routes: " + str(routes))

    return trim_report_text("\n".join(parts), 2400)


def trim_report_text(value: str, limit: int = 2400) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
