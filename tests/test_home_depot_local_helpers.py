from sniperplug.cogs.home_depot_local import _clean_store_id, _validation_error


def test_clean_store_id_does_not_invent_defaults():
    assert _clean_store_id(None) is None
    assert _clean_store_id("") is None
    assert _clean_store_id(" 6213 ") == "6213"


def test_validation_rejects_bad_store_id():
    assert _validation_error("334851114", "06108", "not-a-store") is not None


def test_validation_accepts_valid_sku_zip_and_store():
    assert _validation_error("334851114", "06108", "6213") is None
