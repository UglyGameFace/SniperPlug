from sniperplug.services.autoscan_route_policy import (
    PUBLIC_AUTOSCAN_FALLBACK_ROUTES,
    is_private_promo_route,
    public_autoscan_queries,
)


def test_private_promo_route_detection_catches_cash_and_onepay():
    assert is_private_promo_route("walmart cash eligible")
    assert is_private_promo_route("cash back walmart")
    assert is_private_promo_route("onepay cash rewards")
    assert not is_private_promo_route("rollback")
    assert not is_private_promo_route("open box electronics")


def test_public_autoscan_queries_removes_cash_but_keeps_public_routes():
    routes = public_autoscan_queries(("walmart cash eligible", "rollback", "open box electronics", "cash back walmart"))

    assert "rollback" in routes
    assert "open box electronics" in routes
    assert not any("cash" in route.lower() for route in routes)


def test_public_autoscan_queries_falls_back_when_category_would_be_empty():
    routes = public_autoscan_queries(("walmart cash eligible", "onepay cash rewards", "cash back walmart"))

    assert routes
    assert routes == PUBLIC_AUTOSCAN_FALLBACK_ROUTES
    assert not any(is_private_promo_route(route) for route in routes)


def test_public_autoscan_queries_dedupes_routes_without_readding_private_cash():
    routes = public_autoscan_queries(("Rollback", "rollback", "Walmart Cash", "clearance", "clearance"))

    assert routes == ("Rollback", "clearance")
