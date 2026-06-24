from __future__ import annotations


def test_walmart_cash_modules_import_cleanly():
    import sniperplug.services.walmart_cash_offers as offers
    import sniperplug.services.walmart_cash_pipeline as pipeline
    import sniperplug.services.walmart_promo_classifier as classifier

    assert hasattr(offers, "build_walmart_cash_summary_embed")
    assert hasattr(offers, "find_walmart_cash_offer")
    assert hasattr(offers, "build_walmart_api_probe_embed")
    assert hasattr(classifier, "classify_walmart_promos")
    assert pipeline is not None
