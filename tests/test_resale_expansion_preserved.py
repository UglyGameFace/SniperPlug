from sniperplug.services.resale_hunt import RESALE_HUNT_KEY, RESALE_HUNT_QUERIES


def test_resale_hunt_key_is_preserved():
    assert RESALE_HUNT_KEY == "resale"


def test_resale_hunt_terms_include_condition_specific_queries():
    terms = " ".join(RESALE_HUNT_QUERIES).lower()

    assert "restored" in terms
    assert "refurbished" in terms
    assert "open box" in terms
    assert "like new" in terms
