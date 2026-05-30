from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from sniperplug.models.deal import NormalizedDeal, utc_now_iso
from sniperplug.services.routing import DEFAULT_ROUTE


class _LibsqlAsyncCursor:
    def __init__(self, result: Any):
        self.result = result
        self._rows_cache: list[Any] | None = None

    async def fetchone(self) -> Any | None:
        rows = await self.fetchall()
        return rows[0] if rows else None

    async def fetchall(self) -> list[Any]:
        if self._rows_cache is None:
            rows = await asyncio.to_thread(self._fetchall_sync)
            self._rows_cache = [self._normalize_row(row) for row in rows]
        return self._rows_cache

    def _fetchall_sync(self) -> list[Any]:
        if hasattr(self.result, "fetchall"):
            return list(self.result.fetchall())
        rows = getattr(self.result, "rows", None)
        if rows is not None:
            return list(rows)
        return []

    def _columns(self) -> list[str]:
        description = getattr(self.result, "description", None)
        if description:
            columns: list[str] = []
            for item in description:
                if isinstance(item, (tuple, list)) and item:
                    columns.append(str(item[0]))
                else:
                    columns.append(str(getattr(item, "name", item)))
            return columns
        columns = getattr(self.result, "columns", None)
        if callable(columns):
            columns = columns()
        if columns:
            return [str(column) for column in columns]
        return []

    def _normalize_row(self, row: Any) -> Any:
        if isinstance(row, dict):
            return row
        keys = getattr(row, "keys", None)
        if callable(keys):
            try:
                return {str(key): row[key] for key in keys()}
            except Exception:
                pass
        columns = self._columns()
        if columns:
            try:
                return {columns[index]: row[index] for index in range(min(len(columns), len(row)))}
            except Exception:
                pass
        return row


class _LibsqlAsyncConnection:
    def __init__(self, conn: Any):
        self.conn = conn

    async def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> _LibsqlAsyncCursor:
        def run() -> _LibsqlAsyncCursor:
            if params is None:
                result = self.conn.execute(sql)
            else:
                result = self.conn.execute(sql, tuple(params))
            return _LibsqlAsyncCursor(result)

        return await asyncio.to_thread(run)

    async def executescript(self, script: str) -> None:
        for statement in _split_sql_script(script):
            await self.execute(statement)

    async def commit(self) -> None:
        await asyncio.to_thread(self.conn.commit)

    async def close(self) -> None:
        close = getattr(self.conn, "close", None)
        if callable(close):
            await asyncio.to_thread(close)


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: Any | None = None
        self.backend = "sqlite"

    async def connect(self) -> None:
        turso_url = os.getenv("TURSO_DATABASE_URL", "").strip() or os.getenv("LIBSQL_URL", "").strip()
        turso_token = os.getenv("TURSO_AUTH_TOKEN", "").strip() or os.getenv("LIBSQL_AUTH_TOKEN", "").strip()
        if turso_url or turso_token:
            if not turso_url or not turso_token:
                raise RuntimeError("Turso database config is incomplete. Set both TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.")
            try:
                import libsql
            except ImportError as exc:
                raise RuntimeError("Turso database config is present, but Python package 'libsql' is not installed.") from exc
            self.conn = _LibsqlAsyncConnection(libsql.connect(database=turso_url, auth_token=turso_token))
            self.backend = "turso"
            await self.conn.execute("PRAGMA foreign_keys=ON;")
            await self.conn.commit()
            return

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

    def require_conn(self) -> Any:
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

            CREATE TABLE IF NOT EXISTS price_observations (
                observation_id TEXT PRIMARY KEY,
                retailer TEXT NOT NULL,
                product_key TEXT NOT NULL,
                product_id TEXT,
                sku TEXT,
                upc TEXT,
                store_id TEXT,
                zip_code TEXT,
                title TEXT,
                product_url TEXT,
                current_price REAL NOT NULL,
                reference_price REAL,
                reference_source TEXT,
                source_key TEXT,
                observed_at TEXT NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_price_observations_product_store ON price_observations(retailer, product_key, store_id, observed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_price_observations_product ON price_observations(retailer, product_key, observed_at DESC);
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
            (guild_id, route, channel_id, now, now),
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

    async def record_price_observation(
        self,
        *,
        retailer: str,
        product_key: str,
        current_price: float,
        product_id: str | None = None,
        sku: str | None = None,
        upc: str | None = None,
        store_id: str | None = None,
        zip_code: str | None = None,
        title: str | None = None,
        product_url: str | None = None,
        reference_price: float | None = None,
        reference_source: str | None = None,
        source_key: str | None = None,
    ) -> None:
        if not product_key or current_price <= 0:
            return
        conn = self.require_conn()
        await conn.execute(
            """
            INSERT INTO price_observations (
                observation_id, retailer, product_key, product_id, sku, upc,
                store_id, zip_code, title, product_url, current_price,
                reference_price, reference_source, source_key, observed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                retailer,
                product_key,
                product_id,
                sku,
                upc,
                store_id,
                zip_code,
                title,
                product_url,
                current_price,
                reference_price,
                reference_source,
                source_key,
                utc_now_iso(),
            ),
        )
        await conn.commit()

    async def get_price_reference(
        self,
        *,
        retailer: str,
        product_key: str,
        store_id: str | None = None,
        current_price: float | None = None,
        min_observations: int = 3,
        limit: int = 120,
    ) -> dict[str, Any] | None:
        if not product_key:
            return None
        store_rows = await self._fetch_price_observations(retailer=retailer, product_key=product_key, store_id=store_id, limit=limit) if store_id else []
        rows = store_rows
        scope = "store"
        if len(rows) < min_observations:
            rows = await self._fetch_price_observations(retailer=retailer, product_key=product_key, store_id=None, limit=limit)
            scope = "all-stores"
        if len(rows) < min_observations:
            return {
                "ready": False,
                "observation_count": len(rows),
                "needed": min_observations,
                "scope": scope,
                "source": "SniperPlug learning mode",
            }

        prices = sorted(float(row["current_price"]) for row in rows if row["current_price"] is not None and float(row["current_price"]) > 0)
        if len(prices) < min_observations:
            return None
        high_price = round(max(prices), 2)
        low_price = round(min(prices), 2)
        median_price = round(_median(prices), 2)
        reference_price = high_price
        if current_price is not None and reference_price <= current_price * 1.05:
            return {
                "ready": False,
                "observation_count": len(prices),
                "needed": min_observations,
                "scope": scope,
                "highest_price": high_price,
                "median_price": median_price,
                "lowest_price": low_price,
                "source": "SniperPlug observed price history",
                "reason": "No higher trusted baseline yet.",
            }
        return {
            "ready": True,
            "reference_price": reference_price,
            "source": f"SniperPlug observed high baseline ({scope}, {len(prices)} samples)",
            "observation_count": len(prices),
            "highest_price": high_price,
            "median_price": median_price,
            "lowest_price": low_price,
            "scope": scope,
            "first_seen_at": rows[-1]["observed_at"],
            "last_seen_at": rows[0]["observed_at"],
        }

    async def _fetch_price_observations(self, *, retailer: str, product_key: str, store_id: str | None, limit: int) -> list[Any]:
        conn = self.require_conn()
        safe_limit = max(3, min(limit, 500))
        if store_id:
            cursor = await conn.execute(
                """
                SELECT current_price, observed_at FROM price_observations
                WHERE retailer = ? AND product_key = ? AND store_id = ?
                ORDER BY observed_at DESC
                LIMIT ?
                """,
                (retailer, product_key, store_id, safe_limit),
            )
        else:
            cursor = await conn.execute(
                """
                SELECT current_price, observed_at FROM price_observations
                WHERE retailer = ? AND product_key = ?
                ORDER BY observed_at DESC
                LIMIT ?
                """,
                (retailer, product_key, safe_limit),
            )
        return await cursor.fetchall()

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


def _split_sql_script(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    midpoint = count // 2
    if count % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2
