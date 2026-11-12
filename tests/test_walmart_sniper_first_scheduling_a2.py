"""Phase A2: sniper-first scheduling (2 broad + 2 sniper per batch).

Budget is unchanged at 4 searches/minute/batch. The split moves from
3 broad + 1 sniper to 2 broad + 2 sniper, and the second sniper slot is a
hard-capped priority hot list (phones, consoles, GPUs) every 5th batch.
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
    for plan in simulate_batches(30):
        assert len(plan) == 4
        # Page size 25 is enforced downstream by RESULTS_PER_PAGE; the plan
        # itself never asks for more than page 1 per query.
        assert all(page == 1 for _query, page, _sort, _order in plan)


def test_sniper_share_is_two_of_four_with_pinned_revisit_math() -> None:
    # 145 batches: 29 priority firings, 261 rotation slots = 9 full passes
    # over the 29-query rotation. One batch per minute at the fixed rate, so
    # each rotation query recurs every 145/9 = ~16.1 batches ~= 16 minutes
    # (was ~29 min at 1 sniper/batch; pure 2/batch without priority would be
    # 14.5 min — the priority slot costs ~1.6 min of revisit).
    plans = simulate_batches(145)
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

    assert sniper_total == 290
    assert sum(priority_counts.values()) == 29
    assert rotation_counts == {query: 9 for query in PRICE_SNIPER_QUERIES}
    # ~16.1 batches between repeat visits of the same rotation query.
    assert 145 / 9 == 16.11111111111111


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
    # 1 priority per 5 batches at 1 batch/minute = 12/hour, structurally.
    assert priority_total == 60
    assert priority_total / 5 == 12.0  # per hour at the fixed 60s rate


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
        for batch in range(0, 30, 5)
    }
    assert seen == set(PRIORITY_SNIPER_QUERIES)


def test_priority_slot_never_duplicates_the_rotation_slot() -> None:
    # Batch 5 collides without avoidance: rotation slot 9 is "apple iphone"
    # and the raw priority pick is also "apple iphone". The helper steps to
    # the next hot query instead of spending two searches on one query.
    assert priority_sniper_query_for_batch(5) == "apple iphone"
    assert (
        priority_sniper_query_for_batch(5, avoid="apple iphone")
        == "playstation 5 console"
    )
    plan = simulate_batches(6)[5]
    assert plan[2][0] == "apple iphone"
    assert plan[3][0] == "playstation 5 console"
    assert len({query for query, _page, _sort, _order in plan}) == 4


def test_priority_cap_binds_from_below_and_fails_safe() -> None:
    # Default: every 5 batches, 12/hour.
    assert priority_sniper_interval_batches() == 5
    # Asking for every batch with a 12/hour cap still waits 5 batches.
    assert priority_sniper_interval_batches(every_n_batches=1, max_per_hour=12) == 5
    # A tighter cap stretches the interval: 6/hour -> every 10 batches.
    assert priority_sniper_interval_batches(every_n_batches=5, max_per_hour=6) == 10
    # A looser cap does not shorten the configured interval.
    assert priority_sniper_interval_batches(every_n_batches=5, max_per_hour=60) == 5
    # Invalid values fail safe to the defaults instead of disabling the cap.
    assert priority_sniper_interval_batches(every_n_batches=0, max_per_hour=0) == 5
    assert priority_sniper_interval_batches(every_n_batches=-3, max_per_hour=-1) == 5
    # Counting helper agrees with the firing helper.
    assert priority_snipers_before_batch(0) == 0
    assert priority_snipers_before_batch(1) == 1
    assert priority_snipers_before_batch(5) == 1
    assert priority_snipers_before_batch(6) == 2
    assert priority_snipers_before_batch(145) == 29
    assert priority_sniper_query_for_batch(0) == "iphone"
    assert priority_sniper_query_for_batch(1) is None
    assert priority_sniper_query_for_batch(4) is None
    assert priority_sniper_query_for_batch(5) == "apple iphone"


def test_priority_env_overrides_parse_safely(monkeypatch) -> None:
    monkeypatch.setenv("SNIPERPLUG_PRIORITY_SNIPER_EVERY_N_BATCHES", "10")
    monkeypatch.setenv("SNIPERPLUG_PRIORITY_SNIPER_MAX_PER_HOUR", "6")
    assert priority_sniper_env() == (10, 6)
    monkeypatch.setenv("SNIPERPLUG_PRIORITY_SNIPER_EVERY_N_BATCHES", "banana")
    monkeypatch.setenv("SNIPERPLUG_PRIORITY_SNIPER_MAX_PER_HOUR", "0")
    assert priority_sniper_env() == (5, 12)


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
