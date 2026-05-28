from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from sniperplug.models.deal import NormalizedDeal, utc_now_iso
from sniperplug.services.routing import DEFAULT_ROUTE


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

            CREATE TABLE IF NOT EXISTS guild_alert_channels (
                guild_id INTEGER NOT NULL,
                route TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, route)
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

            CREATE TABLE IF NOT EXISTS clearance_seeds (
                seed_id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                retailer TEXT NOT NULL,
                title TEXT NOT NULL,
                sku TEXT,
                upc TEXT,
                product_url TEXT,
                store_id TEXT,
                zip_code TEXT,
                observed_price REAL,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deal_route_memory (
                guild_id INTEGER NOT NULL,
                retailer TEXT NOT NULL,
                route_query TEXT NOT NULL,
                scans INTEGER NOT NULL DEFAULT 0,
                returned_products INTEGER NOT NULL DEFAULT 0,
                verified_hits INTEGER NOT NULL DEFAULT 0,
                review_hits INTEGER NOT NULL DEFAULT 0,
                flip_hits INTEGER NOT NULL DEFAULT 0,
                blocked_hits INTEGER NOT NULL DEFAULT 0,
                last_score REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, retailer, route_query)
            );

            CREATE INDEX IF NOT EXISTS idx_guild_alert_channels_guild ON guild_alert_channels(guild_id);
            CREATE INDEX IF NOT EXISTS idx_deals_retailer ON deals(retailer);
            CREATE INDEX IF NOT EXISTS idx_deals_discount ON deals(discount_percent);
            CREATE INDEX IF NOT EXISTS idx_deals_last_checked ON deals(last_checked_at);
            CREATE INDEX IF NOT EXISTS idx_clearance_seeds_guild_retailer ON clearance_seeds(guild_id, retailer);
            CREATE INDEX IF NOT EXISTS idx_clearance_seeds_sku ON clearance_seeds(sku);
            CREATE INDEX IF NOT EXISTS idx_clearance_seeds_upc ON clearance_seeds(upc);
            CREATE INDEX IF NOT EXISTS idx_deal_route_memory_guild_retailer_score ON deal_route_memory(guild_id, retailer, last_score DESC);
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
        await self.set_alert_route(guild_id, DEFAULT_ROUTE, channel_id, commit=False)
        await conn.commit()

    async def get_guild_deal_channel(self, guild_id: int) -> int | None:
        default_route = await self.get_alert_route(guild_id, DEFAULT_ROUTE)
        if default_route:
            return default_route

        conn = self.require_conn()
        cursor = await conn.execute(
            "SELECT deals_channel_id FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return int(row["deals_channel_id"]) if row and row["deals_channel_id"] else None

    async def set_alert_route(self, guild_id: int, route: str, channel_id: int, *, commit: bool = True) -> None:
        conn = self.require_conn()
        now = utc_now_iso()
        await conn.execute(
            """
            INSERT INTO guild_alert_channels (guild_id, route, channel_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, route) DO UPDATE SET
                channel_id = excluded.channel_id,
                updated_at = excluded.updated_at
            """,
            (guild_id, route, channel_id, now),
        )
        if commit:
            await conn.commit()

    async def get_alert_route(self, guild_id: int, route: str) -> int | None:
        conn = self.require_conn()
        cursor = await conn.execute(
            "SELECT channel_id FROM guild_alert_channels WHERE guild_id = ? AND route = ?",
            (guild_id, route),
        )
        row = await cursor.fetchone()
        return int(row["channel_id"]) if row else None

    async def get_all_alert_routes(self, guild_id: int) -> dict[str, int]:
        conn = self.require_conn()
        cursor = await conn.execute(
            "SELECT route, channel_id FROM guild_alert_channels WHERE guild_id = ? ORDER BY route",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        return {str(row["route"]): int(row["channel_id"]) for row in rows}

    async def resolve_alert_channel(self, guild_id: int, route: str) -> int | None:
        direct = await self.get_alert_route(guild_id, route)
        if direct:
            return direct
        return await self.get_guild_deal_channel(guild_id)

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

    async def add_clearance_seed(
        self,
        guild_id: int,
        user_id: int,
        retailer: str,
        title: str,
        sku: str | None = None,
        upc: str | None = None,
        product_url: str | None = None,
        store_id: str | None = None,
        zip_code: str | None = None,
        observed_price: float | None = None,
        notes: str | None = None,
    ) -> str:
        conn = self.require_conn()
        now = utc_now_iso()
        seed_id = uuid4().hex
        await conn.execute(
            """
            INSERT INTO clearance_seeds (
                seed_id, guild_id, created_by, retailer, title, sku, upc,
                product_url, store_id, zip_code, observed_price, notes,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                seed_id,
                guild_id,
                user_id,
                retailer,
                title,
                sku,
                upc,
                product_url,
                store_id,
                zip_code,
                observed_price,
                notes,
                now,
                now,
            ),
        )
        await conn.commit()
        return seed_id

    async def list_clearance_seeds(self, guild_id: int, retailer: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        conn = self.require_conn()
        safe_limit = max(1, min(limit, 25))
        if retailer:
            cursor = await conn.execute(
                """
                SELECT * FROM clearance_seeds
                WHERE guild_id = ? AND retailer = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (guild_id, retailer, safe_limit),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT * FROM clearance_seeds
                WHERE guild_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (guild_id, safe_limit),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

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
            "alert_routes": await self.get_all_alert_routes(guild_id),
            "deals_count": deals_count,
            "dead_reports_count": reports_count,
        }
