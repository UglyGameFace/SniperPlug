from sniperplug.cogs.home_depot_search import _broad_query_warning, clean_query


def test_clean_query_collapses_whitespace():
    assert clean_query("  milwaukee    drill  ") == "milwaukee drill"


def test_broad_query_warning_for_generic_terms():
    assert _broad_query_warning("clearance") is not None
    assert _broad_query_warning("tools") is not None
    assert _broad_query_warning("home depot") is not None


def test_broad_query_warning_allows_targeted_terms():
    assert _broad_query_warning("milwaukee drill") is None
    assert _broad_query_warning("ryobi battery") is None
