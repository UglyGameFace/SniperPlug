from sniperplug.services.verified_discount_hunt import CATEGORY_ROUTES, HUNT_PRESETS


def test_beauty_fragrance_hunt_category_exists():
    assert "beauty" in HUNT_PRESETS
    preset = HUNT_PRESETS["beauty"]

    assert preset.label == "Beauty & Fragrance"
    assert any("fragrance" in query for query in preset.queries)
    assert any("cologne" in query for query in preset.queries)
    assert any("perfume" in query for query in preset.queries)


def test_all_hunt_includes_fragrance_surfaces():
    all_queries = CATEGORY_ROUTES["all"][3]

    assert "fragrance clearance" in all_queries
    assert "cologne clearance" in all_queries
    assert "perfume clearance" in all_queries
    assert "designer fragrance clearance" in all_queries
