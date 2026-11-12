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
from sniperplug.services.walmart_price_sniper_routes import PRICE_SNIPER_QUERIES
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
        queries=tuple(f"query-{(start_index + index) % total_routes}" for index in range(2)),
        total_routes=total_routes,
        completed_routes_before=completed_routes,
        completed_passes_before=completed_passes,
    )


def test_catalog_plan_keeps_original_api_budget_but_checks_two_fresh_routes() -> None:
    claim = fake_claim()
    plan = catalog.catalog_request_plan_for_claim(claim)
    assert len(plan) == 4
    assert plan[:2] == tuple((query, 1, None, None) for query in claim.queries)
    assert plan[2] == (PRICE_SNIPER_QUERIES[0], 1, "price", "ascending")
    # Batch 0 is a priority batch: the second sniper slot is the hot list.
    assert plan[3] == ("iphone", 1, "price", "ascending")
    assert catalog.catalog_request_plan_for_claim(claim) == plan


@pytest.mark.parametrize("route_count", [3, 4, 5, 6, 7, 8, 265])
def test_price_sniper_rotation_visits_every_query_evenly(route_count: int) -> None:
    """Exactly 4 searches/batch; 2 sniper slots, priority displaces one per 5.

    Over 145 batches there are 29 priority firings and 261 rotation slots,
    so every rotation query is visited exactly 9 times (~16 min revisit at
    1 batch/minute) and no rotation query is ever skipped by a priority
    search.
    """
    counts: dict[str, int] = defaultdict(int)
    priority_counts: dict[str, int] = defaultdict(int)
    fresh_counts: dict[str, int] = defaultdict(int)
    start = 0
    completed = 0
    passes = 0
    batches = 145
    for _ in range(batches):
        claim = fake_claim(
            total_routes=route_count, start_index=start,
            completed_routes=completed, completed_passes=passes,
        )
        plan = catalog.catalog_request_plan_for_claim(claim)
        assert len(plan) == 4
        expected_broad = tuple((query, 1, None, None) for query in claim.queries)
        if catalog.is_bestseller_probe_batch(completed // 2):
            expected_broad = (expected_broad[0], (claim.queries[1], 1, "bestseller", None))
        assert plan[:2] == expected_broad
        for slot, (query, page, sort_value, order_value) in enumerate(plan[2:], start=2):
            expected_page = (
                2 if slot == 3
                and catalog.priority_sniper_query_for_batch(completed // 2, avoid=plan[2][0]) is None
                and catalog.is_price_sniper_page2_batch(completed // 2)
                else 1
            )
            assert (page, sort_value, order_value) == (expected_page, "price", "ascending")
        # First sniper slot is always the general rotation; the second is
        # the priority hot list on every 5th batch, rotation otherwise.
        batch_number = completed // 2
        counts[plan[2][0]] += 1
        expected_priority = catalog.priority_sniper_query_for_batch(
            batch_number, avoid=plan[2][0],
        )
        if expected_priority is not None:
            assert plan[3][0] == expected_priority
            priority_counts[plan[3][0]] += 1
        else:
            counts[plan[3][0]] += 1
        for fresh_query in claim.queries:
            fresh_counts[fresh_query] += 1
        passes += int(claim.wraps_catalog)
        completed += 2
        start = claim.next_index

    assert counts == {query: 9 for query in PRICE_SNIPER_QUERIES}
    assert sum(priority_counts.values()) == 29
    assert sum(fresh_counts.values()) == 2 * batches


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
            first = await claim_next_catalog_routes(db, route_count=2, now=started)
            assert first is not None
            expected = catalog.catalog_request_plan_for_claim(first)
            await release_catalog_claim(db, first, error="transient source outage", now=started)
            replay = await claim_next_catalog_routes(db, route_count=2, now=started)
            assert replay is not None
            assert replay.start_index == first.start_index
            assert replay.queries == first.queries
            assert catalog.catalog_request_plan_for_claim(replay) == expected

            assert await complete_catalog_claim(db, replay, now=started)
            following = await claim_next_catalog_routes(db, route_count=2, now=started)
            assert following is not None
            assert following.start_index == 2
            assert len(catalog.catalog_request_plan_for_claim(following)) == 4
        finally:
            await conn.close()

    asyncio.run(run())


def test_catalog_scan_executes_exact_plan_not_unbounded_cartesian_product(monkeypatch) -> None:
    async def run() -> None:
        claim = fake_claim()
        requests: list[tuple] = []
        enqueued: list[SourceCandidate] = []

        async def scan(query, page, _page_size, sort, order, _requested_by):
            requests.append((query, page, sort, order))
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
        assert sorted(requests) == sorted(catalog.catalog_request_plan_for_claim(claim))
        assert len(requests) == 4
        assert output.searches_attempted == 4
        assert output.pages_checked == 4
        assert len(enqueued) == 4
        assert any(
            "fresh routes **2**" in note
            and "price-sorted sniper searches **2**" in note
            and "planned searches **4**" in note
            for note in output.warnings
        )
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


def test_distinct_catalog_offers_keep_different_items_and_changed_prices() -> None:
    def item(item_id: str, offer: str, price: float) -> SourceCandidate:
        return SourceCandidate(
            source_key="walmart", retailer="Walmart", title="Premium resale item",
            product_url=f"https://www.walmart.com/ip/{item_id}",
            product_id=item_id, selected_offer_id=offer,
            current_price=price, api_current_price=price,
            seller_name="Walmart", can_add_to_cart=True,
        )
    items = [
        item("900001", "seller-shared", 149.00),
        item("900002", "seller-shared", 4.00),
        item("900001", "seller-shared", 19.00),
        item("900001", "seller-shared", 149.00),
    ]
    distinct = catalog.distinct_catalog_offer_candidates(items)
    assert len(distinct) == 3
    assert [(c.product_id, c.current_price) for c in distinct] == [
        ("900001", 149.00), ("900002", 4.00), ("900001", 19.00),
    ]


def test_catalog_integration_retains_price_variants_until_exact_enqueue(monkeypatch):
    async def run():
        claim = fake_claim()
        variants: list[SourceCandidate] = []
        def candidate(item_id, price):
            return SourceCandidate(
                source_key="walmart", retailer="Walmart",
                title="Unusual resale item",
                product_url=f"https://www.walmart.com/ip/{item_id}",
                product_id=item_id, selected_offer_id="same-seller-offer",
                current_price=price, api_current_price=price,
                can_add_to_cart=True,
            )
        async def scan(*args):
            return ProviderScanResult(
                provider_key="walmart",
                candidates=(candidate("900001", 100.0), candidate("900001", 14.0),
                            candidate("900002", 6.0)),
            )
        async def enqueue(_db, candidates, **kwargs):
            variants.extend(candidates)
            return SimpleNamespace(summary_line=lambda: "no additional writes")
        monkeypatch.setattr(catalog, "run_walmart_autoscan_scan_off_event_loop", scan)
        monkeypatch.setattr(catalog, "enqueue_walmart_exact_verification_candidates_bulk", enqueue)
        result = await catalog.discover_walmart_catalog_candidates(
            requested_by="global_catalog_autoscan",
            preset=HuntPreset("global_catalog_autoscan", "Walmart", "x", "test", claim.queries, 10),
            db=object(), min_discount=10, catalog_claim=claim,
        )
        assert len(variants) == 3
        assert result.products_checked == 12
        assert result.unique_candidates == 3
        assert result.duplicate_offer_rows_removed == 9
        assert result.multi_price_item_ids == 1
        assert sorted((c.product_id, c.current_price) for c in variants) == [
            ("900001", 14.0), ("900001", 100.0), ("900002", 6.0),
        ]
    asyncio.run(run())
