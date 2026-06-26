from sniperplug.services.open_box_autoscan_routes import (
    OPEN_BOX_AUTOSCAN_KEY,
    OPEN_BOX_AUTOSCAN_QUERIES,
)


def test_open_box_autoscan_route_key_exists():
    assert OPEN_BOX_AUTOSCAN_KEY == "open_box"


def test_open_box_terms_include_condition_specific_queries():
    terms = " ".join(OPEN_BOX_AUTOSCAN_QUERIES).lower()

    assert "restored" in terms
    assert "refurbished" in terms
    assert "open box" in terms
    assert "like new" in terms or "like-new" in terms
