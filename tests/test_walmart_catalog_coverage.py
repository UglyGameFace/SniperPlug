from __future__ import annotations

from pathlib import Path

from sniperplug.cogs import native_auto_scan_runner as native
from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.cogs.resilient_auto_scan_runner import (
    _NATIVE_ORIGINAL_BUILDER_ATTR,
    _NATIVE_PATCH_OWNERS_ATTR,
    _install_catalog_coverage_builder,
    _release_catalog_coverage_builder,
)
from sniperplug.services import walmart_catalog_coverage as coverage
from sniperplug.services.verified_discount_hunt import HUNT_PRESETS
from sniperplug.services.walmart_catalog_coverage import (
    CATALOG_PROBE_ROUTES,
    HART_CORE_CLEARANCE_ROUTE,
    SCHEDULED_COVERAGE_SLOT_SECONDS,
    build_complete_broad_preset,
)


REPO = Path(__file__).resolve().parents[1]
RESILIENT_RUNNER = (
    REPO / "sniperplug/cogs/resilient_auto_scan_runner.py"
).read_text(encoding="utf-8")


def test_scheduled_preset_has_four_unique_public_safe_lanes() -> None:
    preset = build_complete_broad_preset(
        HUNT_PRESETS,
        guild_id=1514374173517152418,
        query_count=4,
        now=0,
    )

    assert len(preset.queries) == 4
    assert len({query.lower() for query in preset.queries}) == 4
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
            query_count=4,
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
        "essentials": HuntPreset(
            "essentials", "Essentials", "🧼", "test", ("essential-route",), 50
        ),
    }

    seen: set[str] = set()
    full_pool_slots = len(auto_routes) + len(CATALOG_PROBE_ROUTES)
    for slot in range(full_pool_slots):
        preset = build_complete_broad_preset(
            presets,
            guild_id=0,
            query_count=4,
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


def test_defensive_fallback_cannot_leak_private_promo_routes(monkeypatch) -> None:
    # Empty the normal tools/probe pool so the test genuinely reaches the
    # defensive deal-week fallback instead of filling every slot earlier.
    monkeypatch.setattr(coverage, "AUTO_TOOLS_FALLBACK", ())
    monkeypatch.setattr(coverage, "CATALOG_PROBE_ROUTES", ())

    presets = {
        "deal_week": HuntPreset(
            "deal_week",
            "Deal Week",
            "🔥",
            "test",
            (
                "walmart cash offers",
                "onepay cash rewards",
                "cash back walmart",
                "public fallback clearance",
            ),
            50,
        ),
        "auto_tools": HuntPreset(
            "auto_tools", "Auto & Tools", "🛠️", "test", (), 50
        ),
        "tech": HuntPreset("tech", "Tech", "🎮", "test", (), 50),
        "home": HuntPreset("home", "Home", "🏠", "test", (), 50),
        "beauty": HuntPreset("beauty", "Beauty", "💄", "test", (), 50),
        "toys": HuntPreset("toys", "Toys", "🧸", "test", (), 50),
        "essentials": HuntPreset("essentials", "Essentials", "🧼", "test", (), 50),
    }

    preset = build_complete_broad_preset(
        presets,
        guild_id=0,
        query_count=8,
        now=0,
    )

    assert "public fallback clearance" in preset.queries
    assert not any(
        token in query.lower()
        for query in preset.queries
        for token in ("walmart cash", "cashback", "cash back", "onepay", "one pay")
    )


def test_catalog_builder_ownership_survives_overlapping_cogs() -> None:
    original_builder = native.build_native_broad_preset
    original_saved = getattr(native, _NATIVE_ORIGINAL_BUILDER_ATTR, None)
    original_owners = int(getattr(native, _NATIVE_PATCH_OWNERS_ATTR, 0) or 0)

    # Isolate this regression from any module import state left by other tests.
    setattr(native, _NATIVE_ORIGINAL_BUILDER_ATTR, original_builder)
    setattr(native, _NATIVE_PATCH_OWNERS_ATTR, 0)
    native.build_native_broad_preset = original_builder
    try:
        _install_catalog_coverage_builder()
        _install_catalog_coverage_builder()
        assert int(getattr(native, _NATIVE_PATCH_OWNERS_ATTR)) == 2
        assert native.build_native_broad_preset is build_complete_broad_preset

        _release_catalog_coverage_builder()
        assert int(getattr(native, _NATIVE_PATCH_OWNERS_ATTR)) == 1
        assert native.build_native_broad_preset is build_complete_broad_preset

        _release_catalog_coverage_builder()
        assert int(getattr(native, _NATIVE_PATCH_OWNERS_ATTR)) == 0
        assert native.build_native_broad_preset is original_builder
    finally:
        native.build_native_broad_preset = original_builder
        setattr(native, _NATIVE_PATCH_OWNERS_ATTR, original_owners)
        if original_saved is None:
            try:
                delattr(native, _NATIVE_ORIGINAL_BUILDER_ATTR)
            except AttributeError:
                pass
        else:
            setattr(native, _NATIVE_ORIGINAL_BUILDER_ATTR, original_saved)


def test_production_runner_installs_catalog_builder_and_keeps_four_routes() -> None:
    assert "SCHEDULED_QUERY_COUNT = 4" in RESILIENT_RUNNER
    assert "_install_catalog_coverage_builder()" in RESILIENT_RUNNER
    assert "_release_catalog_coverage_builder()" in RESILIENT_RUNNER
    assert "catalog_coverage_lanes=4" in RESILIENT_RUNNER
