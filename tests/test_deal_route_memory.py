from sniperplug.services.deal_finder_telemetry import SearchRouteStats
from sniperplug.services.deal_route_memory import RouteMemoryRecord, RouteMemoryUpdate, memory_boost_queries, route_memory_lines, update_from_route_stats


def test_route_memory_update_score_weights_quality_hits():
    update = RouteMemoryUpdate(
        route_query="straight talk samsung",
        scans=2,
        returned_products=25,
        verified_hits=1,
        flip_hits=2,
        review_hits=3,
        blocked_hits=1,
    )

    assert update.score == 44.0


def test_update_from_route_stats_ignores_unknown_routes():
    updates = update_from_route_stats(
        [
            SearchRouteStats(query="unknown", pages_checked=1, returned_products=0),
            SearchRouteStats(query="lego clearance", pages_checked=2, returned_products=50),
        ]
    )

    assert len(updates) == 1
    assert updates[0].route_query == "lego clearance"
    assert updates[0].scans == 2
    assert updates[0].returned_products == 50


def test_memory_boost_queries_only_returns_positive_routes():
    records = [
        RouteMemoryRecord("bad", scans=10, returned_products=100, verified_hits=0, review_hits=0, flip_hits=0, blocked_hits=10, last_score=-5),
        RouteMemoryRecord("galaxy phone clearance", scans=2, returned_products=40, verified_hits=1, review_hits=0, flip_hits=1, blocked_hits=0, last_score=31),
    ]

    assert memory_boost_queries(records) == ("galaxy phone clearance",)


def test_route_memory_lines_formats_records():
    records = [RouteMemoryRecord("lego clearance", 3, 75, 1, 2, 1, 0, 36.5)]

    assert route_memory_lines(records) == ["• `lego clearance` — score **36.5** (1 verified, 1 flip, 2 review, 75 products)"]
