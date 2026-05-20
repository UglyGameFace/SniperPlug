from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from sniperplug.models.deal import NormalizedDeal, utc_now_iso


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        db_path = Path(self.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(db_path.as_posix())
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL;")
        await self.conn.execute("PRAGMA foreign_keys=ON;")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    def require_conn(self) -> aiosqlite.Connection:
        if not self.conn:
            raise RuntimeError("Database is not connected.")
        return self.conn

    async def init(self) -> None:
        conn = self.require_conn()

        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                deals_channel_id INTEGER,
                min_discount_percent REAL NOT NULL DEFAULT 40,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deals (
                deal_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                retailer TEXT NOT NULL,
                title TEXT NOT NULL,
                product_url TEXT NOT NULL,
                image_url TEXT,
                current_price REAL,
                typical_price REAL,
                discount_percent REAL,
                savings_amount REAL,
                asin TEXT,
                sku TEXT,
                upc TEXT,
                seller_name TEXT,
                fulfilled_by_amazon INTEGER,
                fulfillment_type TEXT,
                condition TEXT,
                availability_message TEXT,
                is_possible_price_error INTEGER NOT NULL,
                is_ymmv INTEGER NOT NULL,
                risk_level TEXT NOT NULL,
                confidence_score INTEGER NOT NULL,
                risk_flags_json TEXT NOT NULL,
                alert_tags_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_checked_at TEXT NOT NULL,
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS saved_deals (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                deal_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, deal_id)
            );

            CREATE TABLE IF NOT EXISTS dead_reports (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                deal_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, deal_id)
            );

            CREATE INDEX IF NOT EXISTS idx_deals_retailer ON deals(retailer);
            CREATE INDEX IF NOT EXISTS idx_deals_discount ON deals(discount_percent);
            CREATE INDEX IF NOT EXISTS idx_deals_last_checked ON deals(last_checked_at);
            """
        )
        await conn.commit()

    async def set_guild_deal_channel(self, guild_id: int, channel_id: int) -> None:
        conn = self.require_conn()
        now = utc_now_iso()
        await conn.execute(
            """
            INSERT INTO guild_settings (guild_id, deals_channel_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                deals_channel_id = excluded.deals_channel_id,
                updated_at = excluded.updated_at
            """,
            (guild_id, channel_id, now, now),
        )
        await conn.commit()

    async def get_guild_deal_channel(self, guild_id: int) -> int | None:
        conn = self.require_conn()
        cursor = await conn.execute(
            "SELECT deals_channel_id FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return int(row["deals_channel_id"]) if row and row["deals_channel_id"] else None

    async def upsert_deal(self, deal: NormalizedDeal) -> None:
        conn = self.require_conn()

        await conn.execute(
            """
            INSERT INTO deals (
                deal_id, source, retailer, title, product_url, image_url,
                current_price, typical_price, discount_percent, savings_amount,
                asin, sku, upc, seller_name, fulfilled_by_amazon, fulfillment_type,
                condition, availability_message, is_possible_price_error, is_ymmv,
                risk_level, confidence_score, risk_flags_json, alert_tags_json,
                first_seen_at, last_checked_at, expires_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            ON CONFLICT(deal_id) DO UPDATE SET
                current_price = excluded.current_price,
                typical_price = excluded.typical_price,
                discount_percent = excluded.discount_percent,
                savings_amount = excluded.savings_amount,
                seller_name = excluded.seller_name,
                fulfilled_by_amazon = excluded.fulfilled_by_amazon,
                fulfillment_type = excluded.fulfillment_type,
                condition = excluded.condition,
                availability_message = excluded.availability_message,
                is_possible_price_error = excluded.is_possible_price_error,
                is_ymmv = excluded.is_ymmv,
                risk_level = excluded.risk_level,
                confidence_score = excluded.confidence_score,
                risk_flags_json = excluded.risk_flags_json,
                alert_tags_json = excluded.alert_tags_json,
                last_checked_at = excluded.last_checked_at,
                expires_at = excluded.expires_at
            """,
            (
                deal.deal_id,
                deal.source,
                deal.retailer,
                deal.title,
                deal.product_url,
                deal.image_url,
                deal.current_price,
                deal.typical_price,
                deal.discount_percent,
                deal.savings_amount,
                deal.asin,
                deal.sku,
                deal.upc,
                deal.seller_name,
                int(deal.fulfilled_by_amazon) if deal.fulfilled_by_amazon is not None else None,
                deal.fulfillment_type,
                deal.condition,
                deal.availability_message,
                int(deal.is_possible_price_error),
                int(deal.is_ymmv),
                deal.risk_level,
                deal.confidence_score,
                json.dumps(deal.risk_flags),
                json.dumps(deal.alert_tags),
                deal.first_seen_at,
                deal.last_checked_at,
                deal.expires_at,
            ),
        )
        await conn.commit()

    async def save_deal(self, guild_id: int, user_id: int, deal_id: str) -> None:
        await self._insert_user_deal_event("saved_deals", guild_id, user_id, deal_id)

    async def report_dead(self, guild_id: int, user_id: int, deal_id: str) -> None:
        await self._insert_user_deal_event("dead_reports", guild_id, user_id, deal_id)

    async def _insert_user_deal_event(self, table: str, guild_id: int, user_id: int, deal_id: str) -> None:
        if table not in {"saved_deals", "dead_reports"}:
            raise ValueError("Invalid table name.")

        conn = self.require_conn()
        await conn.execute(
            f"""
            INSERT OR IGNORE INTO {table} (guild_id, user_id, deal_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, user_id, deal_id, utc_now_iso()),
        )
        await conn.commit()

    async def stats(self, guild_id: int) -> dict[str, Any]:
        conn = self.require_conn()

        settings_cursor = await conn.execute(
            "SELECT deals_channel_id FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        )
        settings = await settings_cursor.fetchone()

        deals_cursor = await conn.execute("SELECT COUNT(*) AS count FROM deals")
        deals_count = (await deals_cursor.fetchone())["count"]

        reports_cursor = await conn.execute(
            "SELECT COUNT(*) AS count FROM dead_reports WHERE guild_id = ?",
            (guild_id,),
        )
        reports_count = (await reports_cursor.fetchone())["count"]

        return {
            "deals_channel_id": settings["deals_channel_id"] if settings else None,
            "deals_count": deals_count,
            "dead_reports_count": reports_count,
        }
