from __future__ import annotations

import re
from pathlib import Path

from sniperplug.cogs.auto_discovery import (
    DISCOVERY_CHUNK_ROUTES,
    chunk_discovery_queries,
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


def test_full_discovery_is_split_into_queue_checkpointed_chunks() -> None:
    full = resolve_discovery_plan(
        guild_id=1514374173517152418,
        coverage="full",
    )
    chunks = chunk_discovery_queries(full.queries)

    assert len(chunks) == (
        len(full.queries) + DISCOVERY_CHUNK_ROUTES - 1
    ) // DISCOVERY_CHUNK_ROUTES
    assert all(1 <= len(chunk) <= DISCOVERY_CHUNK_ROUTES for chunk in chunks)
    assert tuple(query for chunk in chunks for query in chunk) == full.queries


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


def test_discover_runs_as_durable_background_job() -> None:
    assert "asyncio.create_task(" in AUTO_DISCOVERY
    assert "DISCOVERY_MAX_RUNTIME_SECONDS" in AUTO_DISCOVERY
    assert "interaction.user.send" in AUTO_DISCOVERY
    assert "delivery_kind" in AUTO_DISCOVERY
    assert "job.status_message.edit" in AUTO_DISCOVERY
    assert "_discovery_progress_notice" not in AUTO_DISCOVERY


def test_discover_shares_walmart_runtime_gates_for_long_sweeps() -> None:
    assert "autoscan_runtime.autoscan_lock(guild_id)" in AUTO_DISCOVERY
    assert "async with _WALMART_PROVIDER_OPERATION_LOCK" in AUTO_DISCOVERY
    assert "job.guild_scan_lock.release()" in AUTO_DISCOVERY
    assert "_set_watchdog_phase" in AUTO_DISCOVERY
    assert "discover_" in AUTO_DISCOVERY


def test_discover_shows_exact_cards_privately_but_caps_fresh_public_cards() -> None:
    assert (
        "shown_cards, category_suppressed_cards, category_notes = "
        "apply_category_preferences" in AUTO_DISCOVERY
    )
    assert "cards=shown_cards" in AUTO_DISCOVERY
    assert "fresh_cards = list(fresh_selection.fresh)" in AUTO_DISCOVERY
    assert "public_cards = fresh_cards[: job.max_public_posts]" in AUTO_DISCOVERY
    assert "cards=public_cards" in AUTO_DISCOVERY
    assert "including already-posted duplicates when present" in AUTO_DISCOVERY
    assert "batch_cards_for_limit(private_cards)" in AUTO_DISCOVERY


def test_discover_status_is_one_edited_message_with_cancel_control() -> None:
    assert 'label="Refresh status"' in AUTO_DISCOVERY
    assert 'label="Cancel"' in AUTO_DISCOVERY
    assert "response.edit_message" in AUTO_DISCOVERY
    assert "One status message is edited in place" in AUTO_DISCOVERY
    assert "completed chunk" in AUTO_DISCOVERY.lower()


def test_discover_slash_metadata_fits_discord_limits() -> None:
    descriptions = (
        "Start a broad exact-verified Walmart catalog discovery job.",
        "Quick: 16 routes. Deep: 64. Full: every route in a durable background job.",
        "Fresh verified deals sent publicly; extra exact cards are delivered privately.",
    )
    for description in descriptions:
        assert description in AUTO_DISCOVERY
        assert 1 <= len(description) <= 100

    choice_names = re.findall(r'app_commands\.Choice\(name="([^"]+)"', AUTO_DISCOVERY)
    assert len(choice_names) == 3
    assert all(1 <= len(name) <= 100 for name in choice_names)


def test_command_catalog_explains_discover_vs_global_autoscan() -> None:
    assert "optional immediate Quick, Deep, or Full exact Walmart sweep" in COMMAND_CATALOG
    assert "Normal automatic coverage does not require this command" in COMMAND_CATALOG
    assert "bounded manual autoscan test" in COMMAND_CATALOG
    assert "not required for normal background coverage" in COMMAND_CATALOG
