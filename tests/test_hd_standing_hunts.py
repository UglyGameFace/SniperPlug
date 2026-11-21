"""Standing Home Depot hunts: registration, daily discovery cycle, stop.

The cycle is driven with an injected search so no SerpApi credit is ever
spent in tests; the injected outcomes mirror what the quota-guarded
SerpApi search returns (candidates, credits, quota-blocked).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.hd_penny_tracker import list_hd_penny_items
from sniperplug.services.hd_standing_hunts import (
    HdHuntConfig,
    HuntSearchOutcome,
    StandingHunt,
    list_standing_hunts,
    register_standing_hunt,
    run_hd_hunt_discovery_cycle,
    stop_standing_hunt,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


T0 = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


async def fresh_db() -> FakeDatabase:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    return FakeDatabase(conn)


def candidate(product_id: str, price: float, typical: float) -> SourceCandidate:
    return SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title=f"Item {product_id}",
        product_url=f"https://www.homedepot.com/p/{product_id}",
        current_price=price,
        typical_price=typical,
        product_id=product_id,
    )


def stage_worthy(product_id: str) -> SourceCandidate:
    # .03 ending = final markdown stage; also a deep markdown vs typical.
    return candidate(product_id, 25.03, 99.99)


async def _register_old_hunt(db, *, query="faucet", user_id=111, guild_id=222):
    return await register_standing_hunt(
        db,
        guild_id=guild_id,
        user_id=user_id,
        query=query,
        zip_code="06484",
        store_id="",
        now=T0 - timedelta(hours=25),
    )


def test_register_persists_and_dedupes() -> None:
    async def run() -> None:
        db = await fresh_db()
        hunt = await _register_old_hunt(db)
        assert hunt.active is True
        assert hunt.zip_code == "06484"
        again = await register_standing_hunt(
            db,
            guild_id=222,
            user_id=111,
            query="  Faucet ",
            zip_code="06484",
            store_id="",
            now=T0,
        )
        assert again.hunt_id == hunt.hunt_id
        hunts = await list_standing_hunts(db, guild_id=222, user_id=111)
        assert len(hunts) == 1

    asyncio.run(run())


def test_cycle_seeds_new_candidates_and_dedupes_next_cycle(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    async def run() -> None:
        db = await fresh_db()
        await _register_old_hunt(db)
        calls: list[StandingHunt] = []

        async def fake_search(hunt: StandingHunt) -> HuntSearchOutcome:
            calls.append(hunt)
            return HuntSearchOutcome(
                candidates=(stage_worthy("100123"),), credits_used=1
            )

        config = HdHuntConfig(interval_hours=24)
        summary = await run_hd_hunt_discovery_cycle(
            db, config=config, run_search=fake_search, now=T0
        )
        assert summary.ran == 1 and summary.seeded_new == 1
        items = await list_hd_penny_items(db, status="watching")
        assert [item.product_id for item in items] == ["100123"]

        # Not due again yet: a second pass an hour later is a no-op.
        summary2 = await run_hd_hunt_discovery_cycle(
            db, config=config, run_search=fake_search, now=T0 + timedelta(hours=1)
        )
        assert summary2.ran == 0 and len(calls) == 1

        # A day later it runs again; seeding dedupes against the tracker.
        summary3 = await run_hd_hunt_discovery_cycle(
            db, config=config, run_search=fake_search, now=T0 + timedelta(hours=25)
        )
        assert summary3.ran == 1
        assert summary3.seeded_new == 0 and summary3.seeded_existing == 1
        items = await list_hd_penny_items(db, status="watching")
        assert len(items) == 1

    asyncio.run(run())


def test_cycle_quota_blocked_is_a_skip_not_a_burn(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    async def run() -> None:
        db = await fresh_db()
        hunt = await _register_old_hunt(db)

        async def blocked_search(hunt: StandingHunt) -> HuntSearchOutcome:
            return HuntSearchOutcome(quota_blocked=True)

        summary = await run_hd_hunt_discovery_cycle(
            db,
            config=HdHuntConfig(interval_hours=24),
            run_search=blocked_search,
            now=T0,
        )
        assert summary.ran == 0 and summary.quota_skips == 1
        assert summary.credits_used == 0
        # last_run_at untouched, so the hunt stays due for the next pass.
        hunts = await list_standing_hunts(db, guild_id=222, user_id=111)
        assert hunts[0].last_run_at == hunt.last_run_at

    asyncio.run(run())


def test_stop_removes_hunt_from_cycle_but_tracked_items_survive(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    async def run() -> None:
        db = await fresh_db()
        hunt = await _register_old_hunt(db)

        async def fake_search(h: StandingHunt) -> HuntSearchOutcome:
            return HuntSearchOutcome(
                candidates=(stage_worthy("100777"),), credits_used=1
            )

        config = HdHuntConfig(interval_hours=24)
        summary = await run_hd_hunt_discovery_cycle(
            db, config=config, run_search=fake_search, now=T0
        )
        assert summary.seeded_new == 1

        assert await stop_standing_hunt(db, hunt_id=hunt.hunt_id, user_id=999) is False
        assert await stop_standing_hunt(db, hunt_id=hunt.hunt_id, user_id=111) is True
        assert await list_standing_hunts(db, guild_id=222, user_id=111) == []

        summary2 = await run_hd_hunt_discovery_cycle(
            db, config=config, run_search=fake_search, now=T0 + timedelta(days=2)
        )
        assert summary2.active == 0 and summary2.ran == 0
        # The seeded penny item is still tracked after the hunt stopped.
        items = await list_hd_penny_items(db, status="watching")
        assert [item.product_id for item in items] == ["100777"]

    asyncio.run(run())


def test_cycle_skips_without_serpapi_key(monkeypatch) -> None:
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    async def run() -> None:
        db = await fresh_db()
        await _register_old_hunt(db)
        summary = await run_hd_hunt_discovery_cycle(db, now=T0)
        assert summary.skipped_reason == "SERPAPI_API_KEY is not set"
        assert summary.ran == 0

    asyncio.run(run())
