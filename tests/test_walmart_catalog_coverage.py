from __future__ import annotations

from pathlib import Path

from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.services.verified_discount_hunt import HUNT_PRESETS
from sniperplug.services.walmart_catalog_coverage import (
    CATALOG_PROBE_ROUTES,
    HART_CORE_CLEARANCE_ROUTE,
    SCHEDULED_COVERAGE_SLOT_SECONDS,
    build_complete_broad_preset,
)


REPO = Path(__file__).resolve().parents[1]
RESILIENT_RUNNER = (REPO / "sniperplug/cogs/resilient_auto_scan_runner.py").read_text()


def test_scheduled_preset_has_five_unique_public_safe_lanes() -> None:
    preset = build_complete_broad_preset(
        HUNT_PRESETS,
        guild_id=1514374173517152418,
        query_count=5,
        now=0,
    )

    assert len(preset.queries) == 5
    assert len({query.lower() for query in preset.queries}) == 5
    assert HART_CORE_CLEARANCE_ROUTE in preset.queries
    assert not any(
        token in query.lower()
        for query in preset.queries
        for token in ("walmart cash", "cashback", "cash back", "onepay", "one pay")
    )


def test_hart_brushless_clearance_lane_is_guaranteed_every_scheduled_slot() -> None:
    guild_id = 1357215261001912320
    for slot in range(20):
        preset = build_complete_broad_preset(
            HUNT_PRESETS,
            guild_id=guild_id,
            query_count=5,
            now=slot * SCHEDULED_COVERAGE_SLOT_SECONDS,
        )
        assert HART_CORE_CLEARANCE_ROUTE in preset.queries


def test_consecutive_six_hour_slots_reach_every_auto_tools_route() -> None:
    auto_routes = tuple(f"tool-route-{index}" for index in range(10))
    presets = {
        "deal_week": HuntPreset(
            "deal_week",
            "Deal Week",
            "🔥",
            "test",
            ("walmart deals", "clearance"),
            50,
        ),
        "auto_tools": HuntPreset(
            "auto_tools",
            "Auto & Tools",
            "🛠️",
            "test",
            auto_routes,
            50,
        ),
        "tech": HuntPreset("tech", "Tech", "🎮", "test", ("tech-route",), 50),
        "home": HuntPreset("home", "Home", "🏠", "test", ("home-route",), 50),
        "beauty": HuntPreset("beauty", "Beauty", "💄", "test", ("beauty-route",), 50),
        "toys": HuntPreset("toys", "Toys", "🧸", "test", ("toy-route",), 50),
        "essentials": HuntPreset("essentials", "Essentials", "🧼", "test", ("essential-route",), 50),
    }

    seen: set[str] = set()
    for slot in range(len(auto_routes)):
        preset = build_complete_broad_preset(
            presets,
            guild_id=0,
            query_count=5,
            now=slot * SCHEDULED_COVERAGE_SLOT_SECONDS,
        )
        seen.update(query for query in preset.queries if query.startswith("tool-route-"))

    assert seen == set(auto_routes)


def test_manual_pass_uses_extra_catalog_probes_without_duplicates() -> None:
    preset = build_complete_broad_preset(
        HUNT_PRESETS,
        guild_id=1514374173517152418,
        query_count=8,
        now=0,
    )

    assert len(preset.queries) == 8
    assert len(set(preset.queries)) == 8
    assert HART_CORE_CLEARANCE_ROUTE in preset.queries
    assert any(query in CATALOG_PROBE_ROUTES for query in preset.queries)


def test_production_runner_installs_catalog_builder_and_uses_five_routes() -> None:
    assert "SCHEDULED_QUERY_COUNT = 5" in RESILIENT_RUNNER
    assert "native.build_native_broad_preset = build_complete_broad_preset" in RESILIENT_RUNNER
    assert "catalog_coverage_lanes=5" in RESILIENT_RUNNER
