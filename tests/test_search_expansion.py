from sniperplug.services.search_expansion import expand_walmart_query


def test_expand_phone_query_adds_sale_and_mobile_surfaces():
    plan = expand_walmart_query("galaxy phone")

    assert plan.queries[0] == "galaxy phone"
    assert "galaxy phone rollback" in plan.queries
    assert "galaxy phone clearance" in plan.queries
    assert any("prepaid" in query for query in plan.queries)
    assert any("mobile" in note or "prepaid" in note for note in plan.notes)


def test_expand_household_query_adds_household_surface_without_duplicates():
    plan = expand_walmart_query("laundry detergent")

    assert len(plan.queries) == len(set(query.lower() for query in plan.queries))
    assert "laundry detergent rollback" in plan.queries
    assert any("household" in note for note in plan.notes)


def test_expand_query_respects_max_queries():
    plan = expand_walmart_query("samsung galaxy phone", max_queries=3)

    assert len(plan.queries) == 3


def test_expand_query_uses_overlapping_memory_boosts_only():
    plan = expand_walmart_query(
        "galaxy phone",
        max_queries=8,
        boosted_queries=("galaxy phone prepaid", "lego clearance"),
    )

    assert "galaxy phone prepaid" in plan.queries
    assert "lego clearance" not in plan.queries
    assert any("server-learned" in note for note in plan.notes)


def test_expand_fragrance_query_adds_beauty_surfaces():
    plan = expand_walmart_query("dolce gabbana cologne", max_queries=8)

    assert plan.queries[0] == "dolce gabbana cologne"
    assert "dolce gabbana cologne clearance" in plan.queries
    assert "dolce gabbana cologne fragrance clearance" in plan.queries
    assert "dolce gabbana cologne cologne clearance" in plan.queries
    assert "designer fragrance dolce gabbana cologne" in plan.queries
    assert any("beauty/fragrance" in note for note in plan.notes)
