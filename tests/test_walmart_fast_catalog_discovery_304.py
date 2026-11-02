from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services import walmart_catalog_discovery_only as catalog
from sniperplug.services.walmart_global_catalog_autoscan import (
    GlobalCatalogClaim,
    claim_next_catalog_routes,
    complete_catalog_claim,
    release_catalog_claim,
)


def fake_claim(
    *, total_routes: int = 265, start_index: int = 0,
    completed_routes: int = 0, completed_passes: int = 0,
) -> GlobalCatalogClaim:
    return GlobalCatalogClaim(
        token="lease-token",
        start_index=start_index,
        queries=tuple(f"query-{(start_index + index) % total_routes}" for index in range(3)),
        total_routes=total_routes,
        completed_routes_before=completed_routes,
        completed_passes_before=completed_passes,
    )


def test_catalog_plan_keeps_original_api_budget_but_checks_three_fresh_routes() -> None:
    claim = fake_claim()
    plan = catalog.catalog_page_plan_for_claim(claim)
    assert len(plan) == 4
    assert plan[:3] == tuple((query, 1) for query in claim.queries)
    assert plan[3] == ("query-0", 2)
    assert catalog.catalog_page_plan_for_claim(claim) == plan
    assert all(1 <= page <= catalog.CATALOG_MAX_PAGE for _, page in plan)


@pytest.mark.parametrize("route_count", [3, 4, 5, 6, 7, 8, 265])
def test_catalog_deep_rotation_preserves_all_40_pages_for_every_route(route_count: int) -> None:
    """Exactly 4 searches/batch and every route reaches every deep page.

    Covers awkward wrap boundaries, especially current 265 routes where
    naively rotating the third page slot starves many product categories.
    """
    deep_pages: dict[str, list[int]] = defaultdict(list)
    fresh_counts: dict[str, int] = defaultdict(int)
    start = 0
    completed = 0
    passes = 0

    # Each minute performs 3 fresh and 1 deep request. Over 39*route_count
    # batches, each route must receive one visit to each of pages 2..40.
    for _ in range((catalog.CATALOG_MAX_PAGE - 1) * route_count):
        claim = fake_claim(
            total_routes=route_count, start_index=start,
            completed_routes=completed, completed_passes=passes,
        )
        plan = catalog.catalog_page_plan_for_claim(claim)
        assert len(plan) == 4
        assert plan[:3] == tuple((query, 1) for query in claim.queries)
        assert 2 <= plan[3][1] <= 40

        for query, page in plan:
            if page == 1:
                fresh_counts[query] += 1
            else:
                deep_pages[query].append(page)
        passes += int(claim.wraps_catalog)
        completed += 3
        start = claim.next_index

    for index in range(route_count):
        route = f"query-{index}"
        assert fresh_counts[route] == 3 * (catalog.CATALOG_MAX_PAGE - 1)
        assert sorted(deep_pages[route]) == list(range(2, 41))


def test_claim_replay_preserves_same_searches_and_cursor(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = SimpleNamespace(require_conn=lambda: conn)
        started = datetime(2026, 11, 2, tzinfo=timezone.utc)
        try:
            # Use a reduced synthetic pool. The catalog lease itself must
            # remain the same single global durable owner.
            from sniperplug.services import walmart_global_catalog_autoscan as state
            monkeypatch.setattr(state, "catalog_route_pool", lambda: tuple(
                f"query-{index}" for index in range(7)
            ))
            first = await claim_next_catalog_routes(db, route_count=3, now=started)
            assert first is not None
            expected = catalog.catalog_page_plan_for_claim(first)
            await release_catalog_claim(db, first, error="transient source outage", now=started)
            replay = await claim_next_catalog_routes(db, route_count=3, now=started)
            assert replay is not None
            assert replay.start_index == first.start_index
            assert replay.queries == first.queries
            assert catalog.catalog_page_plan_for_claim(replay) == expected

            assert await complete_catalog_claim(db, replay, now=started)
            following = await claim_next_catalog_routes(db, route_count=3, now=started)
            assert following is not None
            assert following.start_index == 3
            assert len(catalog.catalog_page_plan_for_claim(following)) == 4
        finally:
            await conn.close()

    asyncio.run(run())


def test_catalog_scan_executes_exact_plan_not_unbounded_cartesian_product(monkeypatch) -> None:
    async def run() -> None:
        claim = fake_claim()
        requests: list[tuple[str, int]] = []
        enqueued: list[SourceCandidate] = []

        async def scan(query, page, _page_size, _sort, _order, _requested_by):
            requests.append((query, page))
            idx = len(requests)
            return ProviderScanResult(
                provider_key="walmart",
                candidates=(SourceCandidate(
                    source_key="walmart",
                    retailer="Walmart",
                    title=f"Fresh generic product {idx}",
                    product_url=f"https://www.walmart.com/ip/{800000 + idx}",
                    product_id=str(800000 + idx),
                    current_price=1.00,
                ),),
            )

        async def enqueue(_db, candidates, *, min_discount, source_label):
            enqueued.extend(candidates)
            assert min_discount == 10
            assert source_label.startswith("global_catalog_autoscan:")
            return SimpleNamespace(summary_line=lambda: "queue saved")

        monkeypatch.setattr(catalog, "run_walmart_autoscan_scan_off_event_loop", scan)
        monkeypatch.setattr(catalog, "enqueue_walmart_exact_verification_candidates_bulk", enqueue)

        preset = HuntPreset("global_catalog_autoscan", "Walmart", "x", "fast", claim.queries, 10)
        output = await catalog.discover_walmart_catalog_candidates(
            requested_by="global_catalog_autoscan",
            preset=preset, db=object(), min_discount=10, catalog_claim=claim,
        )
        assert sorted(requests) == sorted(catalog.catalog_page_plan_for_claim(claim))
        assert len(requests) == 4
        assert output.searches_attempted == 4
        assert output.pages_checked == 4
        assert len(enqueued) == 4
        assert any("fresh routes **3**" in note and "planned searches **4**" in note for note in output.warnings)
        assert any("foreground exact checks **0**" in note for note in output.warnings)

    asyncio.run(run())


def test_catalog_scan_rejects_claim_query_mismatch_without_spending_api_calls() -> None:
    claim = fake_claim()
    bad_preset = HuntPreset(
        "global_catalog_autoscan", "Walmart", "x", "bad",
        ("another query",), 10,
    )

    with pytest.raises(ValueError, match="claim/preset routes differ"):
        asyncio.run(catalog.discover_walmart_catalog_candidates(
            requested_by="global_catalog_autoscan", preset=bad_preset,
            db=object(), min_discount=10, catalog_claim=claim,
        ))

def test_catalog_fails_closed_if_future_config_would_expand_http_budget(monkeypatch) -> None:
    claim = fake_claim()
    monkeypatch.setattr(catalog, "CATALOG_SORT_PASSES", ((None, None), ("new", None)))
    with pytest.raises(ValueError, match="request budget exceeded"):
        asyncio.run(catalog.discover_walmart_catalog_candidates(
            requested_by="global_catalog_autoscan",
            preset=HuntPreset(
                "global_catalog_autoscan", "Walmart", "x", "budget",
                claim.queries, 10,
            ),
            db=object(), min_discount=10, catalog_claim=claim,
        ))


def test_catalog_provider_overreturn_cannot_amplify_database_ingress(monkeypatch) -> None:
    async def run() -> None:
        claim = fake_claim()
        calls = 0
        persisted = []

        async def scan(_query, _page, _size, _sort, _order, _requested):
            nonlocal calls
            calls += 1
            start = 950_000 + 200 * calls
            return ProviderScanResult(
                provider_key="walmart",
                candidates=tuple(
                    SourceCandidate(
                        source_key="walmart", retailer="Walmart",
                        title=f"Product {i}", product_url=f"https://www.walmart.com/ip/{start + i}",
                        product_id=str(start + i), current_price=1.0,
                    ) for i in range(90)
                ),
            )

        async def enqueue(_db, candidates, **_kwargs):
            persisted.extend(candidates)
            return SimpleNamespace(summary_line=lambda: "queued")

        monkeypatch.setattr(catalog, "run_walmart_autoscan_scan_off_event_loop", scan)
        monkeypatch.setattr(catalog, "enqueue_walmart_exact_verification_candidates_bulk", enqueue)

        result = await catalog.discover_walmart_catalog_candidates(
            requested_by="global_catalog_autoscan",
            preset=HuntPreset(
                "global_catalog_autoscan", "Walmart", "x", "budget",
                claim.queries, 10,
            ),
            db=object(), min_discount=10, catalog_claim=claim,
        )
        assert calls == 4
        assert len(persisted) == 4 * catalog.hunt.RESULTS_PER_PAGE
        assert result.products_checked == len(persisted)
        assert result.pages_checked == 4
        assert any("capped 90 to" in warning for warning in result.warnings)

    asyncio.run(run())
