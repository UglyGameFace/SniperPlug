from __future__ import annotations


def test_compat_modules_import_cleanly():
    import sniperplug.services.manual_posting_explainer  # noqa: F401
    import sniperplug.services.raw_price_review_patch  # noqa: F401
    import sniperplug.services.walmart_cash_guard  # noqa: F401
    import sniperplug.services.walmart_flip_research_patch  # noqa: F401
    import sniperplug.services.walmart_marketplace_comp_guard  # noqa: F401
    import sniperplug.services.walmart_renderer_install  # noqa: F401
