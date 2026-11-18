"""Audit-honesty fixes (2026-11-17): /walmart cash retired, HP/Target watcher
copy says off-by-design, clearance-bank seeds get a live recheck, wishlist
copy says the broad scan must find the item first.

Every test here FAILS on clean main (fb08fca) and PASSES with the fix.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from types import SimpleNamespace

import aiosqlite
import pytest

import sniperplug.services.dm_deal_alerts as alerts
import sniperplug.services.user_wishlist as wishlist_svc
from sniperplug.cogs.clearance_bank import ClearanceBankCog
from sniperplug.cogs.dm_deal_alerts import DmDealAlertsCog
from sniperplug.cogs.walmart_command import (
    MENU_FUNCTIONS,
    WALMART_SUBCOMMANDS,
    WalmartCommandCog,
    _DELEGATES,
)
from sniperplug.cogs.wishlist import GROUP_DESCRIPTION as WISHLIST_GROUP_DESCRIPTION
from sniperplug.cogs.wishlist import WishlistCog
from sniperplug.services.command_surface import replacement_for
from sniperplug.services.hp_watcher_health import HPWatcherHealth
from sniperplug.services.target_watcher_health import TargetWatcherHealth
from sniperplug.services import walmart_deal_recheck as recheck_mod


# -- item 2: /walmart cash retired ---------------------------------------


def test_walmart_cash_subcommand_is_gone() -> None:
    cog = WalmartCommandCog(bot=None)
    names = {command.name for command in cog.app_command.commands}
    assert "cash" not in names
    assert "cash" not in WALMART_SUBCOMMANDS
    assert "cash" not in _DELEGATES
    assert "cash" not in MENU_FUNCTIONS


def test_retired_walmart_cash_no_longer_points_at_itself() -> None:
    # The old top-level name must not advertise /walmart cash as its
    # replacement: that subcommand no longer exists.
    assert replacement_for("walmart_cash") != "/walmart cash"
    assert replacement_for("walmart_cash") == "/walmart find"


def test_my_alerts_has_no_cash_only_option() -> None:
    cog = WalmartCommandCog(bot=None)
    my_alerts = next(
        command for command in cog.app_command.commands if command.name == "my-alerts"
    )
    assert "walmart_cash_only" not in [param.name for param in my_alerts.parameters]
    dm_cog = DmDealAlertsCog(bot=None)
    assert "walmart_cash_only" not in [
        param.name for param in dm_cog.dm_deals.parameters
    ]


# -- item 4: HP/Target watcher copy states the truthful lane state --------
# (2026-11-18 kept-lanes upgrade supersedes the 2026-11-17 "off by design"
# wording: the lanes are KEPT — HP is off because no watcher is running in
# this deployment yet, and Target waits on TARGET_REDSKY_API_KEY first.)


def _fake_delivery_health():
    return SimpleNamespace(
        eligible_without_post=False,
        events_with_errors=False,
        events_seen=0,
        posted=False,
    )


def test_hp_next_action_says_watcher_is_not_running() -> None:
    from sniperplug.cogs.canonical_public_alerts import _next_action

    action = _next_action(
        selected_retailers={"hp"},
        public_route_ready=True,
        walmart_delivery_ready=True,
        hp_delivery_ready=False,
        target_delivery_ready=False,
        target_location_ready=False,
        delivery_health=_fake_delivery_health(),
        threshold=30,
    )
    assert action is not None
    assert "off by design" not in action
    assert "no HP watcher is running" in action
    assert "repair the HP deployment" not in action
    assert "not healthy" not in action


def test_target_next_action_says_watcher_is_not_running() -> None:
    from sniperplug.cogs.canonical_public_alerts import _next_action

    action = _next_action(
        selected_retailers={"target"},
        public_route_ready=True,
        walmart_delivery_ready=True,
        hp_delivery_ready=False,
        target_delivery_ready=False,
        target_location_ready=False,
        delivery_health=_fake_delivery_health(),
        threshold=30,
    )
    assert action is not None
    assert "off by design" not in action
    assert "no Target watcher is running" in action
    assert "TARGET_REDSKY_API_KEY" in action


def test_hp_watcher_not_started_copy_says_off() -> None:
    line = HPWatcherHealth().summary_line()
    assert "off by design" not in line
    assert "**off**" in line


def test_target_watcher_not_started_copy_waits_for_key() -> None:
    line = TargetWatcherHealth().summary_line()
    assert "off by design" not in line
    assert "waiting for key" in line
    assert "TARGET_REDSKY_API_KEY" in line


# -- item 9: clearance-bank seeds get a live recheck ----------------------


@dataclass
class _FakeCandidate:
    product_id: str
    current_price: float | None
    stock_status: str = "In stock"
    can_add_to_cart: bool | None = True


class _FakeInner:
    def __init__(self, candidate):
        self.candidate = candidate

    def _candidate_from_item(self, payload, request):
        return self.candidate


class _FakeProvider:
    def __init__(self, candidate, payload=None):
        self.inner = _FakeInner(candidate)
        self.payload = payload or {"itemId": "123456789"}
        self.requested_item_id = None

    async def fetch_product_detail_payload(self, item_id):
        self.requested_item_id = item_id
        return self.payload


def _walmart_seed(**overrides):
    seed = {
        "retailer": "walmart",
        "title": "Test item",
        "sku": "123456789",
        "upc": None,
        "product_url": "https://www.walmart.com/ip/Test-Item/123456789",
        "store_id": None,
        "zip_code": None,
        "observed_price": 20.0,
        "notes": "",
    }
    seed.update(overrides)
    return seed


def test_recheck_clearance_seed_verifies_walmart_seed_live() -> None:
    provider = _FakeProvider(_FakeCandidate(product_id="123456789", current_price=20.0))
    result = asyncio.run(recheck_mod.recheck_clearance_seed(provider, _walmart_seed()))
    assert provider.requested_item_id == "123456789"
    assert result.status == "unchanged"
    assert result.item_id == "123456789"


def test_recheck_clearance_seed_reports_price_drop() -> None:
    provider = _FakeProvider(_FakeCandidate(product_id="123456789", current_price=15.0))
    result = asyncio.run(recheck_mod.recheck_clearance_seed(provider, _walmart_seed()))
    assert result.status == "price_changed"
    assert result.current_price == 15.0


def test_recheck_clearance_seed_skips_non_walmart_seed() -> None:
    provider = _FakeProvider(_FakeCandidate(product_id="123456789", current_price=20.0))
    result = asyncio.run(
        recheck_mod.recheck_clearance_seed(provider, _walmart_seed(retailer="home_depot"))
    )
    assert result.status == "unsupported_retailer"
    assert provider.requested_item_id is None


def test_recheck_clearance_seed_refuses_seed_without_item_id() -> None:
    provider = _FakeProvider(_FakeCandidate(product_id="123456789", current_price=20.0))
    result = asyncio.run(
        recheck_mod.recheck_clearance_seed(provider, _walmart_seed(sku=None, product_url=None))
    )
    assert result.status == "identity_missing"
    assert provider.requested_item_id is None


class _FakeDb:
    def __init__(self, seeds):
        self._seeds = seeds

    async def list_clearance_seeds(self, guild_id, retailer=None, limit=10):
        return list(self._seeds)


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred = False

    def is_done(self) -> bool:
        return False

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.deferred = True


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    async def send(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


@pytest.mark.asyncio
async def test_clearance_bank_recheck_shows_live_price(monkeypatch) -> None:
    import sniperplug.cogs.clearance_bank as clearance_bank_mod

    provider = _FakeProvider(_FakeCandidate(product_id="123456789", current_price=15.0))
    monkeypatch.setattr(
        clearance_bank_mod,
        "provider_registry",
        SimpleNamespace(get=lambda key: provider if key == "walmart" else None),
    )
    cog = ClearanceBankCog(bot=SimpleNamespace(db=_FakeDb([_walmart_seed()])))
    interaction = SimpleNamespace(
        guild_id=456,
        user=SimpleNamespace(id=123),
        response=_FakeResponse(),
        followup=_FakeFollowup(),
    )
    await cog.clearance_bank.callback(cog, interaction, recheck=True)
    assert len(interaction.followup.sent) == 1
    _, kwargs = interaction.followup.sent[0]
    embed = kwargs["embed"]
    text = (embed.description or "") + "\n" + "\n".join(
        (field.name or "") + "\n" + (field.value or "") for field in embed.fields
    )
    assert "$15.00" in text
    assert "price_changed" in text or "price changed" in text.lower()


# -- item 11: wishlist copy says the broad scan must find it first --------


def test_wishlist_copy_says_broad_scan_must_find_it_first() -> None:
    assert "broad scan" in WISHLIST_GROUP_DESCRIPTION
    cog = WishlistCog(bot=None)
    assert "broad scan" in (cog.add.description or "")
    assert "broad scan" in wishlist_svc.WISHLIST_SCAN_NOTE


class _FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


@contextlib.asynccontextmanager
async def _isolated_db():
    wishlist_svc._SCHEMA_READY = False
    alerts._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    try:
        yield _FakeDatabase(conn)
    finally:
        await conn.close()
        wishlist_svc._SCHEMA_READY = False
        alerts._SCHEMA_READY = False


class _FakeUser:
    def __init__(self) -> None:
        self.id = 42
        self.dm_sends: list[dict] = []

    async def send(self, *args, **kwargs) -> None:
        self.dm_sends.append(kwargs)


class _FakeInteraction:
    def __init__(self) -> None:
        self.user = _FakeUser()
        self.guild_id = 456
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


@pytest.mark.asyncio
async def test_wishlist_add_success_message_says_broad_scan_first() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="iphone 11")
        assert len(interaction.followup.sent) == 1
        args, _kwargs = interaction.followup.sent[0]
        assert "broad scan" in str(args[0])
