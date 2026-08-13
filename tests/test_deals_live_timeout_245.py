from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from sniperplug.cogs.unified_deal_scanner import (
    DEAL_MODE_VIEW_TIMEOUT_SECONDS,
    DealSearchModeView,
)
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.coordinated_walmart import _scan_priority
from sniperplug.services.deal_finder_engine import (
    INTERACTIVE_MAX_ROUTES,
    interactive_query_routes,
)
from sniperplug.services.deal_search_modes import MODE_BEST
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
