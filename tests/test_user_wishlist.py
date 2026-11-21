"""Tests for the per-user wishlist (service, matching, fanout wiring, cog).

Storage tests use a real in-memory SQLite connection wrapped the same way
the delivery-policy tests do (``require_conn()``). The module-level
``_SCHEMA_READY`` flags are reset before and after every test that touches
a database, because the flag is process-global while each test uses a fresh
connection — leaving it set would make a later test skip table creation on
its own empty connection.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import discord
import pytest

import sniperplug.services.dm_deal_alerts as alerts
import sniperplug.services.user_wishlist as wishlist
from sniperplug.cogs.wishlist import WishlistCog
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.user_wishlist import (
    MAX_WISHLIST_ITEMS_PER_USER,
    add_user_wishlist_item,
    clear_user_wishlist,
    list_user_wishlist,
    list_wishlists_for_users,
    match_wishlist_items,
    remove_user_wishlist_item,
)


class _FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


@contextlib.asynccontextmanager
async def _isolated_db():
    wishlist._SCHEMA_READY = False
    alerts._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    try:
        yield _FakeDatabase(conn)
    finally:
        await conn.close()
        wishlist._SCHEMA_READY = False
        alerts._SCHEMA_READY = False


def _card(
    *,
    title: str = "Apple iPhone 11 64GB Green",
    current: float | None = 99.0,
    reference: float | None = 249.0,
    discount: float | None = 60.2,
    score: int = 120,
    retailer: str = "walmart",
):
    embed = discord.Embed(title=f"🔥 {discount or 0:.0f}% OFF • {title}")
    embed.add_field(name="Product Proof", value=f"Brand: {title.split()[0]}")
    return SimpleNamespace(
        label=title,
        url="https://www.walmart.com/ip/123",
        embed=embed,
        api_current_price=current,
        current_price=current,
        api_reference_price=reference,
        typical_price=reference,
        api_discount_percent=discount,
        discount=discount,
        score=score,
        retailer=retailer,
        variant_attributes={},
    )


def _preference(
    *,
    user_id: int = 42,
    enabled: bool = True,
    min_discount: int = 20,
    categories: tuple[str, ...] = (),
) -> DmDealAlertPreference:
    return DmDealAlertPreference(
        user_id=user_id,
        enabled=enabled,
        min_discount=min_discount,
        categories=categories,
    )


# -- service: storage ---------------------------------------------------


@pytest.mark.asyncio
async def test_add_and_list_roundtrip_is_per_user() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "iphone 11")
        await add_user_wishlist_item(db, 42, "dyson airwrap")
        await add_user_wishlist_item(db, 43, "rtx 5070")

        assert await list_user_wishlist(db, 42) == ("iphone 11", "dyson airwrap")
        assert await list_user_wishlist(db, 43) == ("rtx 5070",)
        assert await list_user_wishlist(db, 44) == ()


@pytest.mark.asyncio
async def test_add_is_case_and_space_insensitive_duplicate() -> None:
    async with _isolated_db() as db:
        first = await add_user_wishlist_item(db, 42, "iPhone  11")
        assert first.added is True
        assert first.item == "iPhone 11"

        second = await add_user_wishlist_item(db, 42, "iphone 11")
        assert second.added is False
        assert second.reason == "duplicate"
        assert await list_user_wishlist(db, 42) == ("iPhone 11",)


@pytest.mark.asyncio
async def test_add_rejects_empty_and_overlong_items() -> None:
    async with _isolated_db() as db:
        assert (await add_user_wishlist_item(db, 42, "   ")).reason == "empty"
        assert (await add_user_wishlist_item(db, 42, "")).reason == "empty"
        long_item = "one two three four five six seven eight nine"
        assert (await add_user_wishlist_item(db, 42, long_item)).reason == "too_long"
        assert await list_user_wishlist(db, 42) == ()


@pytest.mark.asyncio
async def test_cap_is_twenty_items_per_user() -> None:
    async with _isolated_db() as db:
        for index in range(MAX_WISHLIST_ITEMS_PER_USER):
            result = await add_user_wishlist_item(db, 42, f"item {index}")
            assert result.added is True
            assert result.count == index + 1

        over = await add_user_wishlist_item(db, 42, "one more thing")
        assert over.added is False
        assert over.reason == "cap"
        assert over.count == MAX_WISHLIST_ITEMS_PER_USER
        assert len(await list_user_wishlist(db, 42)) == MAX_WISHLIST_ITEMS_PER_USER


@pytest.mark.asyncio
async def test_remove_matches_by_phrase_and_reports_missing() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "iPhone 11")
        removed, display = await remove_user_wishlist_item(db, 42, "IPHONE 11")
        assert removed is True
        assert display == "iPhone 11"
        assert await list_user_wishlist(db, 42) == ()

        removed, _display = await remove_user_wishlist_item(db, 42, "iphone 11")
        assert removed is False


@pytest.mark.asyncio
async def test_clear_removes_only_that_user() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "iphone 11")
        await add_user_wishlist_item(db, 42, "dyson airwrap")
        await add_user_wishlist_item(db, 43, "rtx 5070")

        assert await clear_user_wishlist(db, 42) == 2
        assert await list_user_wishlist(db, 42) == ()
        assert await list_user_wishlist(db, 43) == ("rtx 5070",)
        assert await clear_user_wishlist(db, 42) == 0


@pytest.mark.asyncio
async def test_list_wishlists_for_users_groups_one_query() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "iphone 11")
        await add_user_wishlist_item(db, 43, "rtx 5070")
        await add_user_wishlist_item(db, 43, "lego star wars")

        grouped = await list_wishlists_for_users(db, [42, 43, 44])
        assert grouped == {
            42: ("iphone 11",),
            43: ("rtx 5070", "lego star wars"),
            44: (),
        }
        assert await list_wishlists_for_users(db, []) == {}


# -- service: matching --------------------------------------------------


def test_match_wishlist_items_needs_the_whole_phrase() -> None:
    assert match_wishlist_items(("iphone 11",), "Apple iPhone 11 64GB Green") == "iphone 11"
    assert match_wishlist_items(("iphone 11",), "Apple iPhone 110 case") == ""
    assert match_wishlist_items(("iphone",), "iPhoneX case") == ""
    assert match_wishlist_items(("dyson airwrap",), "Dyson Airwrap Styler") == "dyson airwrap"
    assert match_wishlist_items((), "Apple iPhone 11") == ""
    assert match_wishlist_items(None, "Apple iPhone 11") == ""
    assert match_wishlist_items(("iphone 11",), "") == ""


# -- matching: wishlist intent inside match_dm_deal ---------------------


def test_wishlist_match_is_its_own_inclusion_layer() -> None:
    # The only pick is a deal type this card does not have, so without a
    # wishlist hit nothing matches; the wishlist layer matches on its own.
    preference = _preference(categories=("dm_route:coupon_stack",))
    card = _card()

    assert match_dm_deal(preference, card).matched is False
    decision = match_dm_deal(preference, card, wishlist_items=("iphone 11",))
    assert decision.matched is True
    assert "wishlist: iphone 11" in decision.reason


def test_wishlist_match_bypasses_an_unselected_category_allowlist() -> None:
    preference = _preference(categories=("toys_collectibles",))
    card = _card()
    assert match_dm_deal(preference, card).matched is False
    assert match_dm_deal(
        preference, card, wishlist_items=("iphone 11",)
    ).matched is True


def test_wishlist_match_bypasses_a_mismatched_product_interest() -> None:
    preference = _preference(categories=("interest:airpods",))
    card = _card()
    assert match_dm_deal(preference, card).matched is False
    assert match_dm_deal(
        preference, card, wishlist_items=("iphone 11",)
    ).matched is True


def test_wishlist_match_works_for_a_non_walmart_retailer() -> None:
    preference = _preference(categories=("dm_route:coupon_stack",))
    card = _card(retailer="target")
    assert match_dm_deal(preference, card).matched is False
    assert match_dm_deal(
        preference, card, wishlist_items=("iphone 11",)
    ).matched is True


def test_wishlist_never_lowers_the_discount_floor() -> None:
    preference = _preference()
    cheap_card = _card(current=224.0, reference=249.0, discount=10.0)
    assert match_dm_deal(
        preference, cheap_card, wishlist_items=("iphone 11",)
    ).matched is False


def test_wishlist_never_bypasses_exact_price_proof() -> None:
    preference = _preference()
    unproven = _card(reference=None, discount=None)
    assert match_dm_deal(
        preference, unproven, wishlist_items=("iphone 11",)
    ).matched is False


def test_wishlist_never_matches_when_dm_alerts_are_disabled() -> None:
    preference = _preference(enabled=False)
    assert match_dm_deal(
        preference, _card(), wishlist_items=("iphone 11",)
    ).matched is False


def test_non_matching_wishlist_changes_nothing() -> None:
    preference = _preference()
    assert match_dm_deal(
        preference, _card(), wishlist_items=("rtx 5070",)
    ).matched is True
    # An unselected category still blocks a card no wishlist item matches —
    # and a matching wishlist item is the only thing that lets it through.
    other = _preference(categories=("toys_collectibles",))
    buds = _card(title="Samsung Galaxy Buds Pro")
    assert match_dm_deal(other, buds).matched is False
    assert match_dm_deal(
        other, buds, wishlist_items=("iphone 11",)
    ).matched is False
    assert match_dm_deal(
        other, buds, wishlist_items=("galaxy buds",)
    ).matched is True


# -- fanout wiring ------------------------------------------------------


def test_both_live_fanouts_load_wishlists_for_the_batch() -> None:
    """The DM fanout is where a wishlist becomes a notification. Both live
    fanout paths must load wishlist items and pass them to match_dm_deal —
    a service-only match that no fanout calls would silently never fire."""

    for path in (
        "sniperplug/services/verified_retailer_event_fanout.py",
        "sniperplug/services/walmart_global_deal_fanout_bulk.py",
    ):
        source = Path(path).read_text(encoding="utf-8")
        assert "list_wishlists_for_users" in source
        assert "wishlist_items=wishlists.get(preference.user_id, ())" in source


# -- cog: surface and the delegate trap ---------------------------------


def test_wishlist_is_a_standalone_group_not_a_walmart_delegate() -> None:
    """Regression: /walmart delegates must name a cog bot.py actually loads.
    The wishlist deliberately avoids that map entirely — it is its own
    top-level group, loaded directly in bot.py."""

    from sniperplug.cogs.walmart_command import _DELEGATES

    assert "wishlist" not in _DELEGATES
    cog = WishlistCog(bot=None)
    names = {command.name for command in cog.app_command.commands}
    assert names == {"add", "remove", "list", "clear"}
    assert cog.app_command.name == "wishlist"


def test_bot_loads_the_wishlist_cog_directly() -> None:
    import ast

    bot_path = Path("sniperplug/bot.py")
    tree = ast.parse(bot_path.read_text(encoding="utf-8"))
    loaded: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_cog"):
            continue
        if not call.args or not isinstance(call.args[0], ast.Call):
            continue
        arg_func = call.args[0].func
        if isinstance(arg_func, ast.Name):
            loaded.add(arg_func.id)
    assert "WishlistCog" in loaded
    assert "from sniperplug.cogs.wishlist import WishlistCog" in bot_path.read_text(
        encoding="utf-8"
    )


# -- cog: behavior ------------------------------------------------------


class _FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None

    def is_done(self) -> bool:
        return True

    async def defer(self, *, ephemeral: bool = False) -> None:
        return None

    async def send_message(self, message: str, *, ephemeral: bool = False) -> None:
        self.sent_message = message


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    async def send(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class _FakeUser:
    def __init__(self) -> None:
        self.id = 42
        self.dm_sends: list[dict] = []

    async def send(self, *args, **kwargs) -> None:
        self.dm_sends.append(kwargs)


class _ClosedDmUser(_FakeUser):
    async def send(self, *args, **kwargs) -> None:
        raise discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            "Cannot send messages to this user",
        )


class _FakeInteraction:
    def __init__(self, *, user=None) -> None:
        self.user = user or _FakeUser()
        self.guild_id = 456
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


def _followup_text(interaction: _FakeInteraction) -> str:
    assert len(interaction.followup.sent) == 1
    args, kwargs = interaction.followup.sent[0]
    return str(args[0] if args else kwargs.get("content", ""))


@pytest.mark.asyncio
async def test_cog_add_turns_dm_alerts_on_after_a_dm_probe() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="iphone 11")

        assert await list_user_wishlist(db, 42) == ("iphone 11",)
        assert len(interaction.user.dm_sends) == 1  # confirmation DM probe
        saved = await alerts.get_dm_deal_alert_preference(db, 42)
        assert saved.enabled is True
        assert "iphone 11" in _followup_text(interaction)
        assert "turned them on" in _followup_text(interaction)


@pytest.mark.asyncio
async def test_cog_add_keeps_the_item_when_dms_are_closed() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        interaction = _FakeInteraction(user=_ClosedDmUser())
        await cog.add.callback(cog, interaction, item="iphone 11")

        assert await list_user_wishlist(db, 42) == ("iphone 11",)
        saved = await alerts.get_dm_deal_alert_preference(db, 42)
        assert saved.enabled is False
        assert "could not DM you" in _followup_text(interaction)


@pytest.mark.asyncio
async def test_cog_add_duplicate_and_full_are_plain_words() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        for index in range(MAX_WISHLIST_ITEMS_PER_USER):
            interaction = _FakeInteraction()
            await cog.add.callback(cog, interaction, item=f"item {index}")

        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="item 0")
        assert "already on your wishlist" in _followup_text(interaction)

        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="one more thing")
        assert "wishlist is full" in _followup_text(interaction)


@pytest.mark.asyncio
async def test_cog_remove_list_and_clear_flow() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))

        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="iphone 11")
        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="dyson airwrap")

        interaction = _FakeInteraction()
        await cog.list_items.callback(cog, interaction)
        assert len(interaction.followup.sent) == 1
        _, kwargs = interaction.followup.sent[0]
        embed = kwargs["embed"]
        assert "iphone 11" in (embed.description or "")
        assert "dyson airwrap" in (embed.description or "")

        interaction = _FakeInteraction()
        await cog.remove.callback(cog, interaction, item="iphone 11")
        assert "Removed" in _followup_text(interaction)
        assert await list_user_wishlist(db, 42) == ("dyson airwrap",)

        interaction = _FakeInteraction()
        await cog.remove.callback(cog, interaction, item="iphone 11")
        assert "is not on your wishlist" in _followup_text(interaction)

        interaction = _FakeInteraction()
        await cog.clear.callback(cog, interaction, confirm=False)
        assert "confirm:true" in _followup_text(interaction)
        assert await list_user_wishlist(db, 42) == ("dyson airwrap",)

        interaction = _FakeInteraction()
        await cog.clear.callback(cog, interaction, confirm=True)
        assert "Cleared your wishlist" in _followup_text(interaction)
        assert await list_user_wishlist(db, 42) == ()


@pytest.mark.asyncio
async def test_cog_add_does_not_re_enable_probe_when_already_on() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="iphone 11")
        assert len(interaction.user.dm_sends) == 1

        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="dyson airwrap")
        # Already enabled: no second confirmation DM.
        assert len(interaction.user.dm_sends) == 0
        assert "turned them on" not in _followup_text(interaction)
