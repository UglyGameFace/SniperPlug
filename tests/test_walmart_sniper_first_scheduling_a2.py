"""Phase A2: sniper-first scheduling (2 broad + 2 sniper per batch).

Budget is unchanged at 4 searches/minute/batch. The split moves from
3 broad + 1 sniper to 2 broad + 2 sniper, and the second sniper slot is a
hard-capped priority hot list (phones, consoles, GPUs) every 3rd batch.
"""
from __future__ import annotations

from collections import Counter

from sniperplug.cogs.global_auto_scan_runner import (
    GLOBAL_DISCOVERY_ROUTES_PER_BATCH,
)
from sniperplug.services import walmart_catalog_discovery_only as catalog
from sniperplug.services.walmart_fresh_work_policy import (
    catalog_backpressure_reason,
)
from sniperplug.services.walmart_global_catalog_autoscan import (
    DEFAULT_ROUTES_PER_BATCH,
    GlobalCatalogClaim,
)
from sniperplug.services.walmart_price_sniper_routes import (
    PRICE_SNIPER_QUERIES,
    PRIORITY_SNIPER_QUERIES,
    priority_sniper_interval_batches,
    priority_sniper_query_for_batch,
    priority_snipers_before_batch,
    priority_sniper_env,
)
from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth


TOTAL_ROUTES = 265


def fake_claim(*, start_index: int = 0, completed_routes: int = 0) -> GlobalCatalogClaim:
    return GlobalCatalogClaim(
        token="lease-token",
        start_index=start_index,
        queries=tuple(
            f"query-{(start_index + index) % TOTAL_ROUTES}" for index in range(2)
        ),
        total_routes=TOTAL_ROUTES,
        completed_routes_before=completed_routes,
        completed_passes_before=completed_routes // TOTAL_ROUTES,
    )


def simulate_batches(batch_count: int) -> list[tuple]:
    """Request plans for consecutive batches from a cold cursor."""
    plans = []
    start = 0
    completed = 0
    for _ in range(batch_count):
        claim = fake_claim(start_index=start, completed_routes=completed)
        plans.append(catalog.catalog_request_plan_for_claim(claim))
        completed += 2
        start = claim.next_index
    return plans


def sniper_queries(plan) -> tuple[str, str]:
    return (plan[2][0], plan[3][0])


def test_budget_still_exactly_four_searches_per_batch() -> None:
    assert catalog.GLOBAL_CATALOG_REQUEST_BUDGET == 4
    assert GLOBAL_DISCOVERY_ROUTES_PER_BATCH == 2
    assert DEFAULT_ROUTES_PER_BATCH == 2
    assert catalog.BROAD_ROUTES_PER_BATCH == 2
    assert catalog.SNIPER_SEARCHES_PER_BATCH == 2
    for batch_number, plan in enumerate(simulate_batches(30)):
        assert len(plan) == 4
        # The first broad route and the first sniper always reach page one.
        # The second broad route rotates into depth (2-40) except on
        # bestseller-probe batches, where it keeps its page-1 request. A
        # bounded fraction of ordinary second sniper slots probe page two
        # to bypass page-one accessory clutter.
        assert plan[0][1] == 1
        assert plan[2][1] == 1
        if catalog.is_bestseller_probe_batch(batch_number):
            assert plan[1][1] == 1
        else:
            assert plan[1][1] == catalog.catalog_deep_page_for_batch(batch_number)
        assert plan[3][1] in (1, 2)


def test_sniper_share_is_two_of_four_with_pinned_revisit_math() -> None:
    # 87 batches: 29 priority firings, 145 rotation slots = 5 full passes
    # over the 29-query rotation. One batch per minute at the fixed rate, so
    # each rotation query recurs every 87/5 = 17.4 batches ~= 17 minutes
    # (was ~16.1 min at 1 priority/5 batches; the hotter priority slot costs
    # ~1.3 min of revisit while cutting each hot query's priority check
    # from ~30 to ~18 min).
    plans = simulate_batches(87)
    rotation_counts: Counter[str] = Counter()
    priority_counts: Counter[str] = Counter()
    sniper_total = 0
    for batch_number, plan in enumerate(plans):
        assert len(plan) == 4
        first, second = sniper_queries(plan)
        assert plan[2][2:] == ("price", "ascending")
        assert plan[3][2:] == ("price", "ascending")
        sniper_total += 2
        expected_priority = priority_sniper_query_for_batch(batch_number, avoid=first)
        if expected_priority is not None:
            assert second == expected_priority
            priority_counts[second] += 1
            rotation_counts[first] += 1
        else:
            rotation_counts[first] += 1
            rotation_counts[second] += 1

    assert sniper_total == 174
    assert sum(priority_counts.values()) == 29
    assert rotation_counts == {query: 5 for query in PRICE_SNIPER_QUERIES}
    # ~17.4 batches between repeat visits of the same rotation query.
    assert 87 / 5 == 17.4


def test_broad_cursor_still_advances_and_is_never_starved() -> None:
    # 265 batches cover the whole 265-route pool at 2 routes/batch. The walk
    # step (2) is coprime with 265, so every route is claimed exactly twice
    # and none is skipped: broad is slower (~133 min/full pass, was ~89),
    # never starved.
    plans = simulate_batches(265)
    broad_counts: Counter[str] = Counter()
    for batch_number, plan in enumerate(plans):
        assert len(plan) == 4
        broad_counts[plan[0][0]] += 1
        broad_counts[plan[1][0]] += 1
        # Broad slots are always the claimed routes, in cursor order.
        assert plan[0][2:] == (None, None)
        assert plan[1][2:] == (
            ("bestseller", None) if catalog.is_bestseller_probe_batch(
                batch_number
            ) else (None, None)
        )
    assert broad_counts == {f"query-{index}": 2 for index in range(TOTAL_ROUTES)}
    assert 265 / 2 == 132.5  # ceil -> 133 batches ~= 133 minutes per pass


def test_priority_rotation_is_hard_capped_and_cannot_starve_broad() -> None:
    plans = simulate_batches(300)
    priority_total = 0
    for batch_number, plan in enumerate(plans):
        # The four searches in a batch are always four distinct queries:
        # the priority slot never duplicates the rotation slot it sits next
        # to (the helper steps to the next hot query on a collision).
        assert len({query for query, _page, _sort, _order in plan}) == 4
        expected_priority = priority_sniper_query_for_batch(
            batch_number, avoid=plan[2][0],
        )
        if expected_priority is not None:
            assert plan[3][0] == expected_priority
            priority_total += 1
        # Broad slots are never displaced by a priority search.
        assert plan[0][2:] == (None, None)
        assert plan[1][2:] == (
            ("bestseller", None) if catalog.is_bestseller_probe_batch(batch_number)
            else (None, None)
        )
    # 1 priority per 3 batches at 1 batch/minute = 20/hour, structurally.
    assert priority_total == 100
    assert priority_total / 5 == 20.0  # per hour at the fixed 60s rate


def test_priority_hot_list_covers_phones_consoles_and_gpus() -> None:
    assert PRIORITY_SNIPER_QUERIES == (
        "iphone",
        "apple iphone",
        "playstation 5 console",
        "xbox series x console",
        "nintendo switch console",
        "graphics card",
    )
    assert len(PRIORITY_SNIPER_QUERIES) <= 8, "hot list stays small and explicit"
    # The GPU lane exists only in the hot list, not the general rotation.
    assert "graphics card" in PRIORITY_SNIPER_QUERIES
    assert "graphics card" not in PRICE_SNIPER_QUERIES
    # Every hot query is visited by the priority rotation.
    seen = {
        priority_sniper_query_for_batch(batch)
        for batch in range(0, 18, 3)
    }
    assert seen == set(PRIORITY_SNIPER_QUERIES)


def test_priority_slot_never_duplicates_the_rotation_slot() -> None:
    # Batch 6 collides without avoidance: rotation slot 10 is
    # "playstation 5 console" and the raw priority pick is also
    # "playstation 5 console". The helper steps to the next hot query
    # instead of spending two searches on one query.
    assert priority_sniper_query_for_batch(6) == "playstation 5 console"
    assert (
        priority_sniper_query_for_batch(6, avoid="playstation 5 console")
        == "xbox series x console"
    )
    plan = simulate_batches(7)[6]
    assert plan[2][0] == "playstation 5 console"
    assert plan[3][0] == "xbox series x console"
    assert len({query for query, _page, _sort, _order in plan}) == 4


def test_priority_cap_binds_from_below_and_fails_safe() -> None:
    # Default: every 3 batches, 20/hour.
    assert priority_sniper_interval_batches() == 3
    # Asking for every batch with a 12/hour cap still waits 5 batches.
    assert priority_sniper_interval_batches(every_n_batches=1, max_per_hour=12) == 5
    # A tighter cap stretches the interval: 6/hour -> every 10 batches.
    assert priority_sniper_interval_batches(every_n_batches=5, max_per_hour=6) == 10
    # A looser cap does not shorten the configured interval.
    assert priority_sniper_interval_batches(every_n_batches=5, max_per_hour=60) == 5
    # Invalid values fail safe to the defaults instead of disabling the cap.
    assert priority_sniper_interval_batches(every_n_batches=0, max_per_hour=0) == 3
    assert priority_sniper_interval_batches(every_n_batches=-3, max_per_hour=-1) == 3
    # Counting helper agrees with the firing helper.
    assert priority_snipers_before_batch(0) == 0
    assert priority_snipers_before_batch(1) == 1
    assert priority_snipers_before_batch(3) == 1
    assert priority_snipers_before_batch(5) == 2
    assert priority_snipers_before_batch(6) == 2
    assert priority_snipers_before_batch(145) == 49
    assert priority_sniper_query_for_batch(0) == "iphone"
    assert priority_sniper_query_for_batch(1) is None
    assert priority_sniper_query_for_batch(3) == "apple iphone"
    assert priority_sniper_query_for_batch(4) is None
    assert priority_sniper_query_for_batch(5) is None


def test_priority_env_overrides_parse_safely(monkeypatch) -> None:
    monkeypatch.setenv("SNIPERPLUG_PRIORITY_SNIPER_EVERY_N_BATCHES", "10")
    monkeypatch.setenv("SNIPERPLUG_PRIORITY_SNIPER_MAX_PER_HOUR", "6")
    assert priority_sniper_env() == (10, 6)
    monkeypatch.setenv("SNIPERPLUG_PRIORITY_SNIPER_EVERY_N_BATCHES", "banana")
    monkeypatch.setenv("SNIPERPLUG_PRIORITY_SNIPER_MAX_PER_HOUR", "0")
    assert priority_sniper_env() == (3, 20)


def test_plan_is_replay_deterministic() -> None:
    claim = fake_claim(start_index=42, completed_routes=84)
    first = catalog.catalog_request_plan_for_claim(claim)
    assert first == catalog.catalog_request_plan_for_claim(claim)
    assert len(first) == 4


def test_backpressure_still_pauses_discovery() -> None:
    """The worker checks this before claiming; A2 must not weaken the gate.

    Fresh-work pause at 12 genuinely fresh rows and the 600-row emergency
    stop are unchanged. The plan change touches request shape only — the
    pre-claim backpressure check in the catalog worker is untouched.
    """
    idle = WalmartExactQueueHealth(
        due_now=0, verifying=0, initial_due_now=0,
        confirmed_no_price_due_now=0, recovery_due_now=0, recheck_due_now=0,
        identity_blocked=0,
    )
    assert catalog_backpressure_reason(idle) is None

    fresh_pause = WalmartExactQueueHealth(
        due_now=12, verifying=0, initial_due_now=12,
        confirmed_no_price_due_now=0, recovery_due_now=0, recheck_due_now=0,
        identity_blocked=0,
    )
    reason = catalog_backpressure_reason(fresh_pause)
    assert reason is not None and "fresh exact-detail backpressure" in reason

    below_pause = WalmartExactQueueHealth(
        due_now=11, verifying=0, initial_due_now=11,
        confirmed_no_price_due_now=0, recovery_due_now=0, recheck_due_now=0,
        identity_blocked=0,
    )
    assert catalog_backpressure_reason(below_pause) is None

    emergency = WalmartExactQueueHealth(
        due_now=0, verifying=0, initial_due_now=0,
        confirmed_no_price_due_now=0, recovery_due_now=300, recheck_due_now=300,
        identity_blocked=0,
    )
    reason = catalog_backpressure_reason(emergency)
    assert reason is not None and "emergency backpressure" in reason



def test_bestseller_canary_reuses_one_broad_slot_not_a_fifth_call() -> None:
    plans = simulate_batches(144)
    canaries = [
        (batch, plan)
        for batch, plan in enumerate(plans)
        if plan[1][2] == "bestseller"
    ]
    assert len(canaries) == 12
    assert all(batch % 12 == 7 for batch, _ in canaries)
    assert all(len(plan) == 4 for plan in plans)
    assert all(plan[2][2:] == ("price", "ascending") for plan in plans)
    assert all(plan[3][2:] == ("price", "ascending") for plan in plans)
    assert all(plan[0][2:] == (None, None) for plan in plans)


def test_page_two_probe_never_adds_calls_or_displaces_page_one_safety(monkeypatch):
    plans = simulate_batches(145)
    second_page = [
        (batch, plan) for batch, plan in enumerate(plans)
        if plan[3][1] == 2
    ]
    assert second_page
    # Page-2 probes land on odd batches (offset 1, every 2nd batch) except
    # the odd batches the priority slot takes (every 3rd): 72 odd batches
    # minus 24 odd priority batches = 48 probes over 145 batches (~20/hour).
    assert len(second_page) == 48
    assert all(batch % 2 == 1 for batch, _ in second_page)
    assert all(len(plan) == 4 for plan in plans)
    assert all(plan[2][1] == 1 for plan in plans)
    assert all(plan[0][1] == 1 for plan in plans)
    assert all(
        priority_sniper_query_for_batch(batch, avoid=plan[2][0]) is None
        for batch, plan in second_page
    )
    assert all(plan[3][2:] == ("price", "ascending") for _, plan in second_page)
    monkeypatch.setenv("SNIPERPLUG_PRICE_SNIPER_PAGE2_ENABLED", "false")
    assert all(plan[3][1] == 1 for plan in simulate_batches(145))


def test_cheap_speedups_pin_new_defaults(monkeypatch) -> None:
    """Cheap speed-ups (Nov 16): page-2 every 2nd batch, priority every 3rd.

    The page-2 offset is 1: with an every-2nd-batch cadence, offset 2 could
    never match (batch % 2 is only 0 or 1), so the probe would silently
    stop firing. Priority defaults are every 3 batches at 20/hour — the
    cap alone does not set the cadence (the interval is
    max(every_n_batches, ceil(60 / max_per_hour))), so both defaults moved
    together; the cap-only change would have left the interval at 5.
    """
    assert catalog.PRICE_SNIPER_PAGE2_EVERY_N_BATCHES == 2
    assert catalog.PRICE_SNIPER_PAGE2_BATCH_OFFSET == 1
    assert catalog.is_price_sniper_page2_batch(1)
    assert not catalog.is_price_sniper_page2_batch(0)
    assert not catalog.is_price_sniper_page2_batch(2)
    assert catalog.is_price_sniper_page2_batch(3)
    assert priority_sniper_interval_batches() == 3
    monkeypatch.delenv("SNIPERPLUG_PRIORITY_SNIPER_EVERY_N_BATCHES", raising=False)
    monkeypatch.delenv("SNIPERPLUG_PRIORITY_SNIPER_MAX_PER_HOUR", raising=False)
    assert priority_sniper_env() == (3, 20)


def test_broad_second_route_rotates_into_depth_without_extra_calls(monkeypatch) -> None:
    """Deep-page rotation (deal-discovery improvement 1).

    The second broad route samples a rotating deep page (2-40) instead of
    repeating page 1, so a glitch below result 25 on a broad route is seen
    within one full pass. The first broad route always stays on page 1 so
    fresh listings are never missed, the bestseller probe keeps its own
    page-1 request, and the 4-search budget never changes.
    """
    plans = simulate_batches(145)
    assert all(len(plan) == 4 for plan in plans)
    assert all(plan[0][1] == 1 for plan in plans)
    deep = [
        (batch, plan) for batch, plan in enumerate(plans)
        if plan[1][1] > 1
    ]
    assert deep
    assert all(
        plan[1][1] == catalog.catalog_deep_page_for_batch(batch)
        for batch, plan in deep
    )
    assert all(
        2 <= plan[1][1] <= catalog.CATALOG_MAX_PAGE for _batch, plan in deep
    )
    assert all(
        plan[1][1] == 1
        for batch, plan in enumerate(plans)
        if catalog.is_bestseller_probe_batch(batch)
    )
    # The deep page actually rotates across batches instead of sticking.
    assert len({plan[1][1] for _batch, plan in deep}) > 1
    # Kill switch: disabling the deep page puts the second broad route back
    # on page 1 with no other change to the plan.
    monkeypatch.setenv("SNIPERPLUG_BROAD_DEEP_PAGE_ENABLED", "false")
    assert all(plan[1][1] == 1 for plan in simulate_batches(145))
    assert all(len(plan) == 4 for plan in simulate_batches(145))
