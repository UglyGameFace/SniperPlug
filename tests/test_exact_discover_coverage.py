from __future__ import annotations

from pathlib import Path

from sniperplug.cogs.auto_discovery import (
    resolve_discovery_plan,
    select_discovery_notes,
)
from sniperplug.services.walmart_catalog_coverage import catalog_route_pool


ROOT = Path(__file__).resolve().parents[1]
AUTO_DISCOVERY = (ROOT / "sniperplug/cogs/auto_discovery.py").read_text(
    encoding="utf-8"
)
COMMAND_CATALOG = (ROOT / "sniperplug/services/command_catalog.py").read_text(
    encoding="utf-8"
)


def test_discover_defaults_to_deep_rotating_coverage() -> None:
    plan = resolve_discovery_plan(guild_id=1514374173517152418)

    assert plan.key == "deep"
    assert len(plan.queries) == 64
    assert plan.total_routes == len(catalog_route_pool())
    assert plan.estimated_searches == 128
    assert "64/" in plan.coverage_line()


def test_quick_and_full_discover_coverage_are_explicit() -> None:
    quick = resolve_discovery_plan(
        guild_id=1514374173517152418,
        coverage="quick",
    )
    full = resolve_discovery_plan(
        guild_id=1514374173517152418,
        coverage="full",
    )

    assert len(quick.queries) == 16
    assert quick.estimated_searches == 32
    assert full.queries == catalog_route_pool()
    assert len(full.queries) == full.total_routes
    assert full.estimated_searches == full.total_routes * 2
    assert "all" in full.coverage_line().lower()


def test_discover_notes_prioritize_exact_queue_and_hide_internal_chatter() -> None:
    notes = select_discovery_notes(
        [
            "WALMART_PUBLISHER_ID is blank; using direct links.",
            "Autoscan lightweight scan skipped internal writes.",
            "Official Walmart detail gate: 320 search candidates queued.",
            "Walmart exact-detail queue: discovered 400.",
            "unrelated provider note",
        ],
        limit=3,
    )

    assert notes == [
        "Official Walmart detail gate: 320 search candidates queued.",
        "Walmart exact-detail queue: discovered 400.",
        "unrelated provider note",
    ]


def test_discover_uses_exact_collector_not_legacy_search_only_path() -> None:
    assert "collect_verified_discount_cards_with_observed_memory" in AUTO_DISCOVERY
    assert "normalize_exact_verified_walmart_cards" in AUTO_DISCOVERY
    assert "catalog_route_pool" in AUTO_DISCOVERY
    assert "find_walmart_discovery_deals" not in AUTO_DISCOVERY
    assert 'max_public_posts: app_commands.Range[int, 1, 20] = 10' in AUTO_DISCOVERY
    assert 'user_id=0' in AUTO_DISCOVERY
    assert "Search-only rows cannot become deal cards" in AUTO_DISCOVERY


def test_command_catalog_explains_discover_vs_autoscan() -> None:
    assert "Broad manual Walmart sweep using the exact-detail queue" in COMMAND_CATALOG
    assert "`/autoscan_now` is the smaller diagnostic command" in COMMAND_CATALOG
