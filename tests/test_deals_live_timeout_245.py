from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.cogs.unified_deal_scanner import (
    DEAL_MODE_VIEW_TIMEOUT_SECONDS,
    DealSearchModeView,
    build_compact_deal_card_embed,
    build_deal_mode_menu_embed,
    send_deal_mode_controls,
)
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.coordinated_walmart import _scan_priority
from sniperplug.services.deal_finder_engine import (
    INTERACTIVE_MAX_ROUTES,
    interactive_query_routes,
)
from sniperplug.services.deal_search_modes import DEAL_SEARCH_MODES, MODE_BEST, MODE_HIDDEN
from sniperplug.services.search_expansion import SearchPlan
from sniperplug.services.walmart_request_coordinator import (
    INTERACTIVE_SEARCH_PRIORITY,
    SEARCH_PRIORITY,
    WalmartRequestCoordinator,
)


def test_interactive_deals_route_budget_is_bounded_and_exact_query_first() -> None:
    plan = SearchPlan(
        original_query="phone mount",
        queries=(
            "phone mount",
            "car phone mount",
            "magnetic phone mount",
            "phone mount rollback",
            "phone mount clearance",
            "dashboard phone mount",
            "vent phone mount",
            "wireless phone mount",
            "phone holder",
            "magsafe mount",
        ),
    )

    routes = interactive_query_routes(plan)

    assert len(routes) == INTERACTIVE_MAX_ROUTES == 8
    assert routes[0] == ("phone mount", 1, None, None)
    assert routes[1] == ("phone mount", 2, None, None)
    assert ("phone mount", 1, "price", "ascending") in routes
    assert ("phone mount", 1, "bestseller", None) in routes
    assert all(page == 1 for query, page, _sort, _order in routes if query != "phone mount")


def test_real_discord_user_id_gets_interactive_walmart_search_priority() -> None:
    interactive = ProviderScanRequest(
        source_key="walmart",
        query="phone mount",
        metadata={"requested_by": "123456789012345678"},
    )
    background = ProviderScanRequest(
        source_key="walmart",
        query="clearance",
        metadata={"requested_by": "autoscan"},
    )

    assert _scan_priority(interactive) == INTERACTIVE_SEARCH_PRIORITY
    assert _scan_priority(background) == SEARCH_PRIORITY


def test_interactive_search_waiter_beats_background_search_waiter() -> None:
    coordinator = WalmartRequestCoordinator(capacity=1)
    coordinator.acquire(priority=SEARCH_PRIORITY)
    order: list[str] = []

    def wait(label: str, priority: int) -> None:
        coordinator.acquire(priority=priority, timeout_seconds=3)
        try:
            order.append(label)
            time.sleep(0.02)
        finally:
            coordinator.release()

    background = threading.Thread(target=wait, args=("background", SEARCH_PRIORITY))
    interactive = threading.Thread(
        target=wait,
        args=("interactive", INTERACTIVE_SEARCH_PRIORITY),
    )
    background.start()
    time.sleep(0.03)
    interactive.start()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        snapshot = coordinator.snapshot()
        if (
            snapshot["waiting_search"] == 1
            and snapshot["waiting_interactive"] == 1
        ):
            break
        time.sleep(0.01)

    coordinator.release()
    background.join(timeout=3)
    interactive.join(timeout=3)

    assert order == ["interactive", "background"]


@pytest.mark.asyncio
async def test_deal_mode_menu_stays_alive_for_fifteen_minutes() -> None:
    view = DealSearchModeView(
        cog=SimpleNamespace(),
        query="phone mount",
        min_discount=50,
        max_results=10,
        sort_value=None,
        order_value=None,
        alerts_only=False,
        simple_mode=True,
        mode=MODE_BEST,
    )
    try:
        assert DEAL_MODE_VIEW_TIMEOUT_SECONDS == 15 * 60
        assert view.timeout == DEAL_MODE_VIEW_TIMEOUT_SECONDS
    finally:
        view.stop()


def make_control_view(token: str, mode: str = MODE_BEST) -> DealSearchModeView:
    return DealSearchModeView(
        cog=SimpleNamespace(),
        query="phone mount",
        min_discount=50,
        max_results=10,
        sort_value=None,
        order_value=None,
        alerts_only=False,
        simple_mode=True,
        mode=mode,
        menu_token=token,
        owner_id=123,
    )


def test_each_result_menu_has_unique_component_ids_and_active_mode_style() -> None:
    first = make_control_view("menu-a")
    second = make_control_view("menu-b")
    try:
        first_ids = {item.custom_id for item in first.children if getattr(item, "custom_id", None)}
        second_ids = {item.custom_id for item in second.children if getattr(item, "custom_id", None)}
        assert first_ids.isdisjoint(second_ids)
        assert all("menu-a" in custom_id for custom_id in first_ids)
        best = next(item for item in first.children if getattr(item, "label", None) == "Best Picks")
        hidden = next(item for item in first.children if getattr(item, "label", None) == "Hidden Gems")
        assert best.disabled is True
        assert best.style == discord.ButtonStyle.primary
        assert hidden.disabled is False
        assert hidden.style == discord.ButtonStyle.secondary
    finally:
        first.stop()
        second.stop()


@pytest.mark.asyncio
async def test_view_acknowledges_component_before_button_callback_with_message_update_defer() -> None:
    view = make_control_view("ack-test")

    class Response:
        def __init__(self):
            self.done = False
            self.defer = AsyncMock(side_effect=self._defer)

        async def _defer(self, **kwargs):
            self.done = True

        def is_done(self):
            return self.done

    response = Response()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=response,
        id=999,
        message=SimpleNamespace(id=555),
        data={"custom_id": "deal_mode:ack-test:hidden"},
    )
    try:
        assert await view.interaction_check(interaction) is True
        response.defer.assert_awaited_once_with(thinking=False)
    finally:
        view.stop()


@pytest.mark.asyncio
async def test_mode_control_replaces_existing_menu_instead_of_spawning_duplicate() -> None:
    view = make_control_view("replace-test")
    interaction = SimpleNamespace(
        edit_original_response=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    ranked = SimpleNamespace(mode=DEAL_SEARCH_MODES[MODE_BEST], verified=[], review=[])
    try:
        await send_deal_mode_controls(interaction, view, ranked, replace_existing=True)
        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()
    finally:
        view.stop()


def test_compact_result_card_keeps_price_seller_delivery_links_and_drops_api_noise() -> None:
    source = discord.Embed(
        title="🔥 55% API-derived markdown • Phone Mount",
        url="https://example.com/item",
    )
    source.set_thumbnail(url="https://example.com/image.jpg")
    source.add_field(
        name="💰 Price from Walmart API",
        value=(
            "• Selected-offer item price: **$9.99**\n"
            "• Shipping: **FREE**\n"
            "• Current: **$9.99**\n"
            "• Was/typical: ~~$22.00~~\n"
            "• Delivered-price savings: **$12.01 (55%)**\n"
            "• Discount math status: trusted\n"
            "• Other-seller minimum: $8.00"
        ),
    )
    source.add_field(name="🧾 Product identity from API", value="SKU 123\nUPC 456")
    source.add_field(
        name="🏷️ Offer / seller from API",
        value="• Seller: **Walmart.com**\n• Condition: **New**",
    )
    source.add_field(
        name="📦 Fulfillment / stock from API",
        value="• Stock: **in stock**\n• Fulfillment: **shipping**\n• Shipping: **free**",
    )
    source.add_field(name="🧮 API evidence used", value="lots of internal proof paths")
    source.add_field(name="🔗 Links", value="[App/Web](https://example.com/item)")
    card = DealCard(
        embed=source,
        url="https://example.com/item",
        label="Phone Mount",
        discount=55,
    )
    card.current_price = 9.99

    compact = build_compact_deal_card_embed(card, verified=True)
    names = [field.name for field in compact.fields]
    assert names == ["💰 Price", "🏪 Seller", "📦 Delivery", "🔗 Open product"]
    assert "Product identity" not in str(compact.to_dict())
    assert "API evidence" not in str(compact.to_dict())
    assert "$9.99" in compact.fields[0].value
    assert "55% off" in (compact.description or "")


def test_result_menu_is_compact_instead_of_repeating_a_large_modes_legend() -> None:
    ranked = SimpleNamespace(
        mode=DEAL_SEARCH_MODES[MODE_HIDDEN],
        verified=[],
        review=[object(), object()],
    )
    embed = build_deal_mode_menu_embed("phone mount", ranked)
    assert len(embed.description or "") < 500
    assert all(field.name != "Modes" for field in embed.fields)
    assert any(field.name == "🟨 Private mode" for field in embed.fields)
