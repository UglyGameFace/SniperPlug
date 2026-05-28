from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.deal_finder_telemetry import SearchRouteStats, merge_route_stats, tag_candidates_with_route, top_route_lines


def test_tag_candidates_with_route_records_source_query():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Test Item",
        product_url="https://www.walmart.com/ip/1",
    )

    tag_candidates_with_route([candidate], query="galaxy phone rollback")
    tag_candidates_with_route([candidate], query="galaxy phone clearance")

    assert candidate.variant_attributes["finderSourceQuery"] == "galaxy phone rollback"
    assert candidate.variant_attributes["finderSourceQueries"] == "galaxy phone rollback | galaxy phone clearance"


def test_merge_route_stats_groups_and_sorts_by_productivity():
    merged = merge_route_stats(
        [
            SearchRouteStats(query="weak", pages_checked=1, returned_products=2),
            SearchRouteStats(query="strong", pages_checked=1, returned_products=25),
            SearchRouteStats(query="weak", pages_checked=1, returned_products=3),
        ]
    )

    assert merged[0].query == "strong"
    weak = next(stat for stat in merged if stat.query == "weak")
    assert weak.pages_checked == 2
    assert weak.returned_products == 5


def test_top_route_lines_formats_productive_routes():
    lines = top_route_lines([SearchRouteStats(query="clearance", pages_checked=3, returned_products=75)], limit=1)

    assert lines == ["• `clearance` — **75** products across **3** page(s)"]
