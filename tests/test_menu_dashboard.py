"""Tests for the menu dashboard state snapshot loader.

Chunk 1 of the SniperPlug menu redesign (2026-11-17): only
``sniperplug.services.menu_dashboard_state`` is under test here. The loader
gathers every existing reader into one snapshot and must never raise: a
single failed reader degrades to a safe default instead of blanking the
whole snapshot.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from sniperplug.providers.base import ProviderHealth, ProviderStatus
from sniperplug.providers.registry import ProviderRegistry
from sniperplug.services import menu_dashboard_state as mds
from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    save_dm_deal_alert_preference,
)
from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.user_wishlist import add_user_wishlist_item
from sniperplug.services.walmart_extreme_price_fanout import (
    set_extreme_alerts_enabled,
)
from sniperplug.services.deal_threshold_settings import set_starting_deal_percent


GUILD = 123456789
USER = 987654321


@pytest.fixture(autouse=True)
def _reset_schema_ready_flags():
    """DM prefs and wishlist guard table setup with a process-wide flag.

    Every test here uses a fresh in-memory db, so the flag must be reset
    per test or the second test's ensure would skip creating its tables
    (same pattern as test_auditfix_fanout_extreme.py).
    """
    import sniperplug.services.dm_deal_alerts as dm_alerts
    import sniperplug.services.user_wishlist as wishlist

    dm_alerts._SCHEMA_READY = False
    wishlist._SCHEMA_READY = False
    yield
    dm_alerts._SCHEMA_READY = False
    wishlist._SCHEMA_READY = False

# Exact contract: other chunks import these field names. If this set drifts,
# the menus break.
EXPECTED_FIELDS = {
    "state_available",
    "provider_status",
    "provider_message",
    "threshold_percent",
    "extreme_enabled",
    "extreme_recent",
    "delivery_ready",
    "delivery_summary",
    "dm_enabled",
    "dm_threshold",
    "dm_daily_cap",
    "wishlist_count",
    "wishlist_cap",
    "active_deals",
    "active_deal_count",
    "deals_today",
}


class _FakeProvider:
    """Stand-in for the registered Walmart provider."""

    def __init__(
        self,
        status: ProviderStatus = ProviderStatus.READY,
        message: str = "Ready: Walmart Affiliate API credentials are configured.",
        ok: bool = True,
    ) -> None:
        self._health = ProviderHealth(
            provider_key="walmart", ok=ok, status=status, message=message
        )

    async def healthcheck(self) -> ProviderHealth:
        return self._health


async def _open_db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = SimpleNamespace(require_conn=lambda: conn)
    return db, conn


def _fake_bot(db, provider=None):
    return SimpleNamespace(
        db=db,
        walmart_provider=provider,
        get_guild=lambda guild_id: None,
    )


async def _seed_active_deal(db, conn, *, key, title, discount, seen_at):
    await conn.execute(
        """
        INSERT INTO guild_active_deal_cache
            (guild_id, active_key, retailer, title, url, current_price,
             discount, score, source_label, status, first_seen_at, last_seen_at)
        VALUES (?, ?, 'walmart', ?, ?, ?, ?, 100, 'verified', 'active', ?, ?)
        """,
        (GUILD, key, title, f"https://www.walmart.com/ip/{key}", 10.0, discount, seen_at, seen_at),
    )


async def _seed_posted_deal(db, conn, *, key, status, posted_at):
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        """
        INSERT INTO guild_public_deal_posts
            (guild_id, deal_key, retailer, source_label, status, first_seen_at, posted_at)
        VALUES (?, ?, 'walmart', 'verified', ?, ?, ?)
        """,
        (GUILD, key, status, now, posted_at),
    )


@pytest.mark.asyncio
async def test_happy_path_load_has_every_field() -> None:
    db, conn = await _open_db()
    try:
        await ensure_public_post_tables(db)
        await set_starting_deal_percent(db, GUILD, 55)
        await set_extreme_alerts_enabled(db, GUILD, True)
        await save_dm_deal_alert_preference(
            db,
            DmDealAlertPreference(
                user_id=USER, enabled=True, min_discount=20, max_alerts_per_day=10
            ),
        )
        await add_user_wishlist_item(db, USER, "iphone 11")
        await add_user_wishlist_item(db, USER, "dyson airwrap")

        now = datetime.now(timezone.utc)
        # Four active deals, staggered so "recent" order is deterministic.
        await _seed_active_deal(db, conn, key="d1", title="Deal newest", discount=50, seen_at=now.isoformat())
        await _seed_active_deal(db, conn, key="d2", title="Deal 2", discount=45, seen_at=(now - timedelta(hours=1)).isoformat())
        await _seed_active_deal(db, conn, key="d3", title="Deal 3", discount=60, seen_at=(now - timedelta(hours=2)).isoformat())
        await _seed_active_deal(db, conn, key="d4", title="Deal oldest", discount=30, seen_at=(now - timedelta(hours=3)).isoformat())
        await conn.commit()

        # Two posted today, one posted yesterday, one merely reserved today.
        await _seed_posted_deal(db, conn, key="p1", status="posted", posted_at=now.isoformat())
        await _seed_posted_deal(db, conn, key="p2", status="posted", posted_at=now.isoformat())
        await _seed_posted_deal(db, conn, key="p3", status="posted", posted_at=(now - timedelta(hours=26)).isoformat())
        await _seed_posted_deal(db, conn, key="p4", status="reserved", posted_at=None)
        await conn.commit()

        state = await mds.load_menu_dashboard_state(_fake_bot(db, _FakeProvider()), GUILD, USER)

        assert {f.name for f in dataclasses.fields(state)} == EXPECTED_FIELDS
        assert state.state_available is True
        assert state.provider_status == "READY"
        assert "Ready" in state.provider_message
        assert state.threshold_percent == 55
        assert state.extreme_enabled is True
        assert state.extreme_recent == 0  # queue is empty in this seed
        assert state.delivery_ready is False  # no public route configured
        assert isinstance(state.delivery_summary, str) and state.delivery_summary
        assert state.dm_enabled is True
        assert state.dm_threshold == 20
        assert state.dm_daily_cap == 10
        assert state.wishlist_count == 2
        assert state.wishlist_cap == 20
        assert state.active_deal_count == 4
        assert len(state.active_deals) == 3  # top 3 most recent
        assert state.active_deals[0]["title"] == "Deal newest"
        for deal in state.active_deals:
            assert set(deal) == {"title", "price", "discount"}
        assert state.deals_today == 2
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_provider_disabled_renders_disabled_not_a_crash() -> None:
    db, conn = await _open_db()
    try:
        provider = _FakeProvider(
            status=ProviderStatus.DISABLED,
            message="Disabled: set WALMART_PROVIDER_ENABLED=true after credentials are configured.",
            ok=False,
        )
        state = await mds.load_menu_dashboard_state(_fake_bot(db, provider), GUILD, USER)

        assert state.state_available is True
        assert state.provider_status == "DISABLED"
        assert "Disabled" in state.provider_message
        assert state.threshold_percent == 40
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_provider_error_and_missing_provider() -> None:
    db, conn = await _open_db()
    try:
        error_provider = _FakeProvider(
            status=ProviderStatus.ERROR,
            message="Missing Walmart config: consumer id.",
            ok=False,
        )
        state = await mds.load_menu_dashboard_state(_fake_bot(db, error_provider), GUILD, USER)
        assert state.provider_status == "ERROR"
        assert "Missing Walmart config" in state.provider_message

        # No provider anywhere: UNKNOWN, never a crash, never a guessed status.
        bot = _fake_bot(db, None)
        empty_registry = ProviderRegistry()
        import sniperplug.services.menu_dashboard_state as loader_module

        real_registry = loader_module.provider_registry
        loader_module.provider_registry = empty_registry
        try:
            state = await mds.load_menu_dashboard_state(bot, GUILD, USER)
        finally:
            loader_module.provider_registry = real_registry
        assert state.provider_status == "UNKNOWN"
        assert state.provider_message
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_guild_config_renders_defaults() -> None:
    db, conn = await _open_db()
    try:
        state = await mds.load_menu_dashboard_state(_fake_bot(db, _FakeProvider()), GUILD, USER)

        assert state.state_available is True
        assert state.threshold_percent == 40
        assert state.wishlist_count == 0
        assert state.wishlist_cap == 20
        assert state.extreme_enabled is False
        assert state.extreme_recent == 0
        assert state.dm_enabled is False
        assert state.active_deal_count == 0
        assert state.active_deals == []
        assert state.deals_today == 0  # table created by the loader, no posts yet
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_failing_reader_degrades_instead_of_raising(monkeypatch) -> None:
    db, conn = await _open_db()
    try:
        await set_starting_deal_percent(db, GUILD, 55)

        async def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(mds, "get_starting_deal_percent", _boom)
        monkeypatch.setattr(mds, "extreme_alerts_enabled", _boom)
        monkeypatch.setattr(mds, "list_active_deals", _boom)

        state = await mds.load_menu_dashboard_state(_fake_bot(db, _FakeProvider()), GUILD, USER)

        assert state.state_available is True
        assert state.provider_status == "READY"  # healthy reader still loads
        assert state.threshold_percent == 40  # failed reader falls back
        assert state.extreme_enabled is False
        assert state.active_deal_count == 0
        assert state.active_deals == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_deals_today_none_when_no_posted_at_column() -> None:
    db, conn = await _open_db()
    try:
        # Legacy-shaped posted-deals table: no posted_at column, no migration.
        await conn.execute(
            """
            CREATE TABLE guild_public_deal_posts (
                guild_id INTEGER NOT NULL,
                deal_key TEXT NOT NULL,
                retailer TEXT NOT NULL,
                source_label TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, deal_key)
            )
            """
        )
        await conn.commit()

        state = await mds.load_menu_dashboard_state(_fake_bot(db, _FakeProvider()), GUILD, USER)

        assert state.state_available is True
        assert state.deals_today is None
        assert state.threshold_percent == 40
        assert state.active_deal_count == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_no_db_returns_unavailable_snapshot() -> None:
    state = await mds.load_menu_dashboard_state(SimpleNamespace(db=None), GUILD, USER)
    assert state.state_available is False
    assert state.provider_status == "UNKNOWN"
    assert state.threshold_percent == 40
    assert state.wishlist_count == 0
    assert state.active_deal_count == 0
    assert state.active_deals == []
    assert state.deals_today is None

    def _raise():
        raise RuntimeError("Database is not connected.")

    broken = SimpleNamespace(db=SimpleNamespace(require_conn=_raise))
    state = await mds.load_menu_dashboard_state(broken, GUILD, USER)
    assert state.state_available is False
    assert state.provider_status == "UNKNOWN"


@pytest.mark.asyncio
async def test_dm_daily_cap_unlimited_maps_to_none() -> None:
    db, conn = await _open_db()
    try:
        await save_dm_deal_alert_preference(
            db,
            DmDealAlertPreference(
                user_id=USER, enabled=True, min_discount=20, max_alerts_per_day=0
            ),
        )
        state = await mds.load_menu_dashboard_state(_fake_bot(db, _FakeProvider()), GUILD, USER)
        assert state.dm_enabled is True
        assert state.dm_threshold == 20
        assert state.dm_daily_cap is None  # 0 means unlimited, not a cap of 0
    finally:
        await conn.close()
