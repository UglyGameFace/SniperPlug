from sniperplug.cogs.native_auto_scan_runner import build_native_broad_preset
from sniperplug.services.walmart_catalog_coverage import (
    BROAD_DEPARTMENT_ROUTES,
    ROTATION_SECONDS,
    catalog_route_pool,
    rotating_catalog_slice,
)


def test_catalog_route_pool_spans_departments_and_walmart_cash() -> None:
    pool = catalog_route_pool()
    lowered = {query.lower() for query in pool}

    assert len(pool) == len(lowered)
    assert len(pool) > 100
    assert "walmart electronics" in lowered
    assert "walmart grocery" in lowered
    assert "tool clearance" in lowered
    assert any("walmart cash" in query for query in lowered)
    assert set(BROAD_DEPARTMENT_ROUTES).issubset(set(pool))


def test_catalog_rotation_advances_without_repeating_same_slice() -> None:
    first = rotating_catalog_slice(guild_id=123, query_count=8, now=0)
    second = rotating_catalog_slice(guild_id=123, query_count=8, now=ROTATION_SECONDS)

    assert first.total_routes == second.total_routes
    assert first.slot_count > 1
    assert first.slot_index != second.slot_index
    assert first.queries != second.queries
    assert len(first.queries) == 8
    assert len(second.queries) == 8


def test_native_preset_uses_catalog_rotation_including_cash_discovery() -> None:
    coverage = rotating_catalog_slice(guild_id=999, query_count=8, now=0)
    preset = build_native_broad_preset(
        None,
        guild_id=999,
        query_count=8,
        coverage=coverage,
    )

    assert preset.key == "catalog_wide_rotating"
    assert preset.label == "Catalog-Wide Rotating Sweep"
    assert preset.queries == coverage.queries
    assert len(preset.queries) == 8
    assert "Walmart Cash" in preset.description


def test_coverage_summary_is_truthful_about_bounded_pass() -> None:
    coverage = rotating_catalog_slice(guild_id=1, query_count=4, now=0)
    summary = coverage.summary_line()

    assert "this pass **4** route(s)" in summary
    assert f"full route pool **{coverage.total_routes}**" in summary
    assert "global exact-detail queue" in summary
