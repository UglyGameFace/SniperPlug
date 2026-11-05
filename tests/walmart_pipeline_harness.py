"""End-to-end Walmart pipeline harness.

Every test built on this harness runs the *production* call chain:

    Walmart Affiliate search  -> discover_walmart_catalog_candidates()
                              -> exact-detail queue (bulk enqueue, SQLite)
    AutoScanRunnerCog.run_exact_worker_cycle()
                              -> claim -> batched Product Lookup -> per-item
                                 exact verification -> classification ->
                                 bulk persistence/offer memory
                              -> extreme-price lane -> verified-markdown lane
                              -> public_deal_posts -> Discord channel.send

Only two things are fake: Walmart's HTTP endpoint (``urllib.request.urlopen``
is replaced by an in-memory catalog that speaks the Affiliate API's JSON
shapes) and Discord's gateway objects (guilds/channels that record sends).
Nothing inside SniperPlug is monkeypatched, so a test cannot pass by calling a
helper that production never reaches.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


API_HOST = "developer.api.walmart.com"


def walmart_item(
    item_id: int,
    name: str,
    sale_price: float,
    *,
    msrp: float | None = None,
    was_price: float | None = None,
    category: str = "Electronics/Tablets/All Tablets",
    seller: str = "Walmart.com",
    stock: str = "Available",
    available_online: bool = True,
    marketplace: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an Affiliate API v2 item payload."""

    item: dict[str, Any] = {
        "itemId": int(item_id),
        "name": name,
        "salePrice": float(sale_price),
        "categoryPath": category,
        "stock": stock,
        "availableOnline": bool(available_online),
        "sellerInfo": seller,
        "marketplace": bool(marketplace),
        "largeImage": f"https://i5.walmartimages.com/asr/{item_id}.jpg",
        "productTrackingUrl": f"https://www.walmart.com/ip/{item_id}",
    }
    if msrp is not None:
        item["msrp"] = float(msrp)
    if was_price is not None:
        item["wasPrice"] = float(was_price)
    if extra:
        item.update(extra)
    return item


class _Response(io.BytesIO):
    status = 200

    def __init__(self, payload: Any):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def getcode(self) -> int:
        return 200


@dataclass
class FakeWalmartAPI:
    """In-memory Walmart Affiliate API (search + Product Lookup)."""

    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    search_index: dict[str, list[str]] = field(default_factory=dict)
    hidden_from_search: set[str] = field(default_factory=set)
    requests: list[str] = field(default_factory=list)
    lookup_failures: dict[str, int] = field(default_factory=dict)

    def add(self, item: dict[str, Any], *, queries: tuple[str, ...] = ()) -> str:
        item_id = str(item["itemId"])
        self.items[item_id] = item
        for query in queries:
            self.search_index.setdefault(query.lower(), []).append(item_id)
        return item_id

    def set_price(self, item_id: str | int, price: float) -> None:
        self.items[str(item_id)]["salePrice"] = float(price)

    def remove(self, item_id: str | int) -> None:
        self.items.pop(str(item_id), None)

    # -- request accounting -------------------------------------------------
    def count(self, kind: str) -> int:
        return sum(1 for entry in self.requests if entry.startswith(kind + ":"))

    # -- urlopen replacement -------------------------------------------------
    def urlopen(self, request: Any, timeout: float | None = None, **_kw: Any):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname != API_HOST:
            # Walmart HTML product pages are never reachable in tests.
            self.requests.append(f"page:{parsed.path}")
            raise urllib.error.URLError("walmart.com HTML disabled in test harness")
        params = urllib.parse.parse_qs(parsed.query)
        path = parsed.path
        if path.endswith("/search"):
            query = (params.get("query") or [""])[0]
            self.requests.append(f"search:{query}")
            return _Response(self._search(query, params))
        if "/items" in path:
            tail = path.rsplit("/items", 1)[1].strip("/")
            ids = [tail] if tail else (params.get("ids") or [""])[0].split(",")
            ids = [value for value in ids if value]
            self.requests.append(f"lookup:{','.join(ids)}")
            found = []
            for item_id in ids:
                remaining = self.lookup_failures.get(item_id, 0)
                if remaining > 0:
                    self.lookup_failures[item_id] = remaining - 1
                    continue
                if item_id in self.items:
                    found.append(json.loads(json.dumps(self.items[item_id])))
            if not found:
                body = json.dumps(
                    {"errors": [{"code": 6001, "message": "Results not found"}]}
                ).encode("utf-8")
                raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(body))
            if tail:
                return _Response(found[0])
            return _Response({"items": found})
        self.requests.append(f"other:{path}")
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"{}"))

    def _search(self, query: str, params: dict[str, list[str]]) -> dict[str, Any]:
        lowered = query.lower().strip()
        if lowered.isdigit():
            ids = [lowered] if lowered in self.items else []
        elif lowered in self.search_index:
            ids = list(self.search_index[lowered])
        else:
            words = [w for w in lowered.split() if w]
            ids = [
                item_id
                for item_id, item in self.items.items()
                if words and all(w in str(item.get("name", "")).lower() for w in words)
            ]
        ids = [i for i in ids if i in self.items and i not in self.hidden_from_search]
        if (params.get("sort") or [""])[0] == "price":
            reverse = (params.get("order") or ["ascending"])[0] == "descending"
            ids.sort(key=lambda i: float(self.items[i].get("salePrice") or 0), reverse=reverse)
        start = max(1, int((params.get("start") or ["1"])[0]))
        size = max(1, int((params.get("numItems") or ["25"])[0]))
        page = ids[start - 1 : start - 1 + size]
        return {
            "query": query,
            "totalResults": len(ids),
            "start": start,
            "numItems": len(page),
            "items": [json.loads(json.dumps(self.items[i])) for i in page],
        }


# ---------------------------------------------------------------- Discord ----
class FakeMessage:
    def __init__(self, message_id: int, embed: Any, view: Any = None):
        self.id = message_id
        self.embed = embed
        self.embeds = [embed] if embed is not None else []
        self.view = view
        self.edits = 0

    async def edit(self, *, embed: Any = None, view: Any = "unchanged", **_kw: Any):
        if embed is not None:
            self.embed = embed
            self.embeds = [embed]
        if view != "unchanged":
            self.view = view
        self.edits += 1
        return self


class FakeChannel:
    def __init__(self, channel_id: int, guild: "FakeGuild", name: str = "sniperplug-deals"):
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.sent: list[Any] = []
        self.messages: dict[int, FakeMessage] = {}
        self.fail_with: Exception | None = None

    async def send(self, *, embed: Any = None, view: Any = None, **_kw: Any) -> FakeMessage:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(embed)
        message = FakeMessage(len(self.sent) + self.id, embed, view)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        message = self.messages.get(int(message_id))
        if message is None:
            import discord

            raise discord.NotFound(None, "Unknown Message")
        return message

    def titles(self) -> list[str]:
        return [str(getattr(embed, "title", "") or "") for embed in self.sent]


class FakeGuild:
    def __init__(self, guild_id: int):
        self.id = guild_id
        self.name = f"guild-{guild_id}"
        self.me = None
        self.unavailable = False
        self.channel = FakeChannel(guild_id + 1, self)
        self.text_channels = [self.channel]

    def get_channel(self, channel_id: int):
        return self.channel if int(channel_id) == self.channel.id else None


class FakeBot:
    def __init__(self, db: Any, guilds: list[FakeGuild]):
        self.db = db
        self.all_guilds = list(guilds)
        self.guilds = list(guilds)
        self.user = None

    def get_guild(self, guild_id: int):
        for guild in self.guilds:
            if int(guild.id) == int(guild_id):
                return guild
        return None

    def get_channel(self, channel_id: int):
        for guild in self.guilds:
            if guild.channel.id == int(channel_id):
                return guild.channel
        return None

    async def fetch_channel(self, channel_id: int):
        for guild in self.guilds:
            if guild.channel.id == int(channel_id):
                return guild.channel
        raise LookupError(channel_id)

    def get_user(self, _user_id: int):
        return None

    async def fetch_user(self, _user_id: int):
        return None

    async def wait_until_ready(self) -> None:
        return None

    def is_ready(self) -> bool:
        return True

    def add_view(self, *_a: Any, **_k: Any) -> None:
        return None


# ------------------------------------------------- Turso-shaped row adapter ---
class _MappingCursor:
    """Cursor whose rows are plain dicts, exactly like the Turso/libSQL path."""

    def __init__(self, cursor: Any):
        self._cursor = cursor
        self.rowcount = getattr(cursor, "rowcount", -1)
        self.lastrowid = getattr(cursor, "lastrowid", None)
        self.description = getattr(cursor, "description", None)

    def _convert(self, row: Any) -> Any:
        if row is None:
            return None
        keys = row.keys() if hasattr(row, "keys") else None
        if keys:
            return {str(key): row[key] for key in keys}
        return row

    async def fetchone(self):
        return self._convert(await self._cursor.fetchone())

    async def fetchall(self):
        return [self._convert(row) for row in await self._cursor.fetchall()]


class MappingRowConnection:
    """Wrap an aiosqlite connection so every row is a mapping (no row[0])."""

    def __init__(self, conn: Any):
        self._conn = conn

    async def execute(self, sql: str, params: Any = None):
        return _MappingCursor(await self._conn.execute(sql, params or ()))

    async def executescript(self, script: str):
        return await self._conn.executescript(script)

    async def commit(self):
        await self._conn.commit()

    async def rollback(self):
        await self._conn.rollback()

    async def close(self):
        await self._conn.close()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


# ---------------------------------------------------------------- pipeline ---
class Pipeline:
    """Owns a fresh SQLite DB, the production provider stack and the cog."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        *,
        guild_ids: tuple[int, ...] = (111000,),
        mapping_rows: bool = False,
    ):
        self.mapping_rows = mapping_rows
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.api = FakeWalmartAPI()
        self.guilds = [FakeGuild(guild_id) for guild_id in guild_ids]
        self.db = None
        self.bot = None
        self.cog = None

    async def start(self) -> "Pipeline":
        from sniperplug.providers.cached_walmart import CachedWalmartProvider
        from sniperplug.providers.registry import provider_registry
        from sniperplug.providers.walmart import WalmartAffiliateConfig
        from sniperplug.providers.walmart_page_recovery import WalmartPageRecoveryProvider
        from sniperplug.storage.db import Database

        for name in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "LIBSQL_URL", "LIBSQL_AUTH_TOKEN"):
            self.monkeypatch.delenv(name, raising=False)
        self.monkeypatch.setattr(urllib.request, "urlopen", self.api.urlopen)

        # Process-wide "schema ready" flags assume one database per process
        # (true in production). Each test gets a fresh database file.
        from sniperplug.services import dm_deal_alerts, dm_delivery_policy

        self.monkeypatch.setattr(dm_deal_alerts, "_SCHEMA_READY", False)
        self.monkeypatch.setattr(dm_delivery_policy, "_SCHEMA_READY", False)

        db = Database(str(self.tmp_path / "pipeline.sqlite3"))
        await db.connect()
        if self.mapping_rows:
            db.conn = MappingRowConnection(db.conn)
        await db.init()
        self.db = db

        class _TestWalmartProvider(WalmartPageRecoveryProvider):
            def _signed_headers(self) -> dict[str, str]:  # no real RSA key in tests
                return {"WM_CONSUMER.ID": "test"}

        base = _TestWalmartProvider(
            WalmartAffiliateConfig(
                consumer_id="test-consumer",
                private_key_b64="dGVzdA==",
                publisher_id="",
                enabled=True,
                timeout_seconds=5,
            )
        )
        provider_registry.clear()
        provider_registry.register(CachedWalmartProvider(db, base))
        self.base_provider = base

        self.bot = FakeBot(db, self.guilds)
        from sniperplug.cogs.global_auto_scan_runner import AutoScanRunnerCog

        self.cog = AutoScanRunnerCog(self.bot)
        return self

    async def close(self) -> None:
        from sniperplug.providers.registry import provider_registry
        from sniperplug.providers.walmart_lookup_batch_cache import clear_prefetched_items

        clear_prefetched_items(getattr(self, "base_provider", None))
        provider_registry.clear()
        if self.db is not None:
            await self.db.close()

    async def enroll(
        self,
        guild: FakeGuild,
        *,
        threshold: int = 40,
        extreme: bool = False,
    ) -> None:
        from sniperplug.services.deal_threshold_settings import set_starting_deal_percent
        from sniperplug.services.public_alert_config import set_public_alert_config
        from sniperplug.services.walmart_extreme_price_fanout import set_extreme_alerts_enabled

        await set_public_alert_config(
            self.db,
            guild_id=guild.id,
            enabled=True,
            retailers=("walmart",),
            channel_id=guild.channel.id,
        )
        await set_starting_deal_percent(self.db, guild.id, threshold)
        if extreme:
            await set_extreme_alerts_enabled(self.db, guild.id, True)

    async def discover(self, *queries: str) -> Any:
        from sniperplug.cogs.deal_scanner import HuntPreset
        from sniperplug.services.walmart_catalog_discovery_only import (
            discover_walmart_catalog_candidates,
        )

        preset = HuntPreset(
            key="e2e",
            label="E2E",
            emoji="t",
            description="end-to-end",
            queries=tuple(queries),
            min_discount=10,
        )
        return await discover_walmart_catalog_candidates(
            requested_by="global_catalog_autoscan",
            preset=preset,
            db=self.db,
            min_discount=10,
        )

    async def cycle(self, times: int = 1) -> list[Any]:
        results = []
        for _ in range(times):
            results.append(await self.cog.run_exact_worker_cycle())
        return results

    async def queue_row(self, item_id: str | int) -> dict[str, Any] | None:
        from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE

        cursor = await self.db.require_conn().execute(
            f"SELECT * FROM {QUEUE_TABLE} WHERE item_id = ?", (str(item_id),)
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def make_due(self, item_id: str | int) -> None:
        """Simulate the weekly safety-recheck floor elapsing for one queue row.

        Verified rows are re-claimed only after their status floor (24h
        markdown, 72h reference, 7d no-reference) measured from verified_at,
        so both timestamps are moved into the past.
        """

        from datetime import datetime, timedelta, timezone

        from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE

        conn = self.db.require_conn()
        cursor = await conn.execute(
            f"SELECT verified_at FROM {QUEUE_TABLE} WHERE item_id = ?", (str(item_id),)
        )
        row = await cursor.fetchone()
        verified_at = None
        if row is not None and row["verified_at"]:
            verified_at = (
                datetime.fromisoformat(str(row["verified_at"])) - timedelta(days=8)
            ).astimezone(timezone.utc).isoformat()
        await conn.execute(
            f"UPDATE {QUEUE_TABLE} SET next_attempt_at = '2000-01-01T00:00:00+00:00', "
            "verified_at = COALESCE(?, verified_at), "
            "lease_until = NULL, lease_token = '' WHERE item_id = ?",
            (verified_at, str(item_id)),
        )
        await conn.commit()

    async def age_offer_memory(self, item_id: str | int, *, hours: float) -> None:
        """Move remembered observations into the past (stable-price maturity)."""

        from datetime import datetime, timedelta, timezone

        from sniperplug.services.walmart_global_offer_memory import GLOBAL_OFFER_MEMORY_TABLE

        conn = self.db.require_conn()
        cursor = await conn.execute(
            f"SELECT identity_key, candidate_last_seen_at FROM {GLOBAL_OFFER_MEMORY_TABLE} WHERE item_id = ?",
            (str(item_id),),
        )
        for row in await cursor.fetchall():
            raw = str(row["candidate_last_seen_at"])
            moved = (datetime.fromisoformat(raw) - timedelta(hours=hours)).astimezone(timezone.utc)
            await conn.execute(
                f"UPDATE {GLOBAL_OFFER_MEMORY_TABLE} SET candidate_last_seen_at = ? WHERE identity_key = ?",
                (moved.isoformat(), row["identity_key"]),
            )
        await conn.commit()

    async def expire_event_leases(self) -> None:
        from sniperplug.services.walmart_global_deal_fanout import EVENT_TABLE

        conn = self.db.require_conn()
        await conn.execute(f"UPDATE {EVENT_TABLE} SET lease_until = NULL")
        await conn.commit()

    async def sweep_expired_posts(self, *, make_stale: bool = True):
        """Run the posted-deal expiry sweep now (as if its pacing elapsed)."""

        from sniperplug.providers.registry import provider_registry
        from sniperplug.services.walmart_posted_deal_expiry import (
            TABLE,
            sweep_posted_walmart_deals,
        )

        if make_stale:
            conn = self.db.require_conn()
            await conn.execute(
                f"UPDATE {TABLE} SET last_checked_at = '2000-01-01T00:00:00+00:00'"
            )
            await conn.commit()
        return await sweep_posted_walmart_deals(
            self.bot,
            provider=provider_registry.get("walmart"),
            force=True,
        )

    def messages(self, guild: FakeGuild | None = None) -> list[FakeMessage]:
        target = guild or self.guilds[0]
        return list(target.channel.messages.values())

    def posts(self, guild: FakeGuild | None = None) -> list[Any]:
        target = guild or self.guilds[0]
        return list(target.channel.sent)
