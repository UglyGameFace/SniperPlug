from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

DEFAULT_NTFY_SERVER = "https://ntfy.sh"


@dataclass(slots=True)
class VerizonNtfySource:
    guild_id: int
    topic: str
    server_url: str = DEFAULT_NTFY_SERVER
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    @property
    def publish_url(self) -> str:
        return f"{self.server_url.rstrip('/')}/{quote(self.topic)}"

    @property
    def poll_url(self) -> str:
        return f"{self.publish_url}/json?poll=1"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_topic(guild_id: int) -> str:
    return f"sniperplug-vz-{guild_id}-{secrets.token_urlsafe(12).lower().replace('_', '-') }"


class VerizonNtfyStore:
    def __init__(self, db: Any):
        self.db = db
        self._schema_ready = False

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        conn = self.db.require_conn()
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS verizon_shine_ntfy_sources (
                guild_id INTEGER PRIMARY KEY,
                topic TEXT NOT NULL,
                server_url TEXT NOT NULL DEFAULT 'https://ntfy.sh',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS verizon_shine_ntfy_events (
                guild_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                event_json TEXT,
                seen_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, event_id)
            );

            CREATE INDEX IF NOT EXISTS idx_verizon_shine_ntfy_enabled
                ON verizon_shine_ntfy_sources(enabled, updated_at DESC);
            """
        )
        await conn.commit()
        self._schema_ready = True

    async def create_or_replace_source(self, guild_id: int, *, server_url: str = DEFAULT_NTFY_SERVER) -> VerizonNtfySource:
        await self.ensure_schema()
        conn = self.db.require_conn()
        now = utc_now_iso()
        source = VerizonNtfySource(guild_id=guild_id, topic=make_topic(guild_id), server_url=server_url.rstrip("/"), enabled=True, created_at=now, updated_at=now)
        await conn.execute(
            """
            INSERT INTO verizon_shine_ntfy_sources (guild_id, topic, server_url, enabled, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                topic = excluded.topic,
                server_url = excluded.server_url,
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (source.guild_id, source.topic, source.server_url, now, now),
        )
        await conn.commit()
        return source

    async def get_source(self, guild_id: int) -> VerizonNtfySource | None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute("SELECT * FROM verizon_shine_ntfy_sources WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()
        return self._source_from_row(row) if row else None

    async def set_enabled(self, guild_id: int, enabled: bool) -> VerizonNtfySource | None:
        await self.ensure_schema()
        source = await self.get_source(guild_id)
        if not source:
            return None
        conn = self.db.require_conn()
        now = utc_now_iso()
        await conn.execute(
            "UPDATE verizon_shine_ntfy_sources SET enabled = ?, updated_at = ? WHERE guild_id = ?",
            (int(enabled), now, guild_id),
        )
        await conn.commit()
        source.enabled = enabled
        source.updated_at = now
        return source

    async def list_enabled_sources(self) -> list[VerizonNtfySource]:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute("SELECT * FROM verizon_shine_ntfy_sources WHERE enabled = 1 ORDER BY updated_at DESC")
        rows = await cursor.fetchall()
        return [self._source_from_row(row) for row in rows]

    async def mark_seen_once(self, guild_id: int, event_id: str, payload: dict[str, Any]) -> bool:
        await self.ensure_schema()
        conn = self.db.require_conn()
        now = utc_now_iso()
        cursor = await conn.execute(
            "SELECT 1 FROM verizon_shine_ntfy_events WHERE guild_id = ? AND event_id = ?",
            (guild_id, event_id),
        )
        if await cursor.fetchone():
            return False
        await conn.execute(
            """
            INSERT INTO verizon_shine_ntfy_events (guild_id, event_id, event_json, seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, event_id, json.dumps(payload), now),
        )
        await conn.commit()
        return True

    def _source_from_row(self, row: Any) -> VerizonNtfySource:
        return VerizonNtfySource(
            guild_id=int(row["guild_id"]),
            topic=str(row["topic"]),
            server_url=str(row["server_url"] or DEFAULT_NTFY_SERVER).rstrip("/"),
            enabled=bool(row["enabled"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
