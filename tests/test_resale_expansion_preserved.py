from sniperplug.services.walmart_discovery_expansion import EXPANDED_PRESETS, RESALE_HUNT_KEY


def test_expanded_presets_include_resale():
    assert RESALE_HUNT_KEY in EXPANDED_PRESETS
    assert EXPANDED_PRESETS[RESALE_HUNT_KEY].label == "Resale Hunt"


def test_resale_terms_include_condition_specific_queries():
    terms = " ".join(EXPANDED_PRESETS[RESALE_HUNT_KEY].queries).lower()
    assert "restored" in terms
    assert "refurbished" in terms
    assert "open box" in terms
    assert "like new" in terms
