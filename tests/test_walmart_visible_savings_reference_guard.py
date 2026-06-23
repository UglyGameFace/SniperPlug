from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartProvider
from sniperplug.services.walmart_savings_reference_patch import install_walmart_savings_reference_patch


def test_walmart_visible_savings_beats_inflated_msrp_reference():
    install_walmart_savings_reference_patch()
    item = {
        "itemId": 884116398806,
        "name": "Dell E2222H 22 inch Class Full HD LCD Monitor",
        "salePrice": 79.95,
        "msrp": 191.39,
        "youSave": 40.04,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=884116398806",
        "stock": "Available",
        "availableOnline": True,
    }

    candidate = WalmartProvider(configured=True)._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="monitor"))

    assert candidate is not None
    assert candidate.current_price == 79.95
    assert candidate.typical_price == 119.99
    assert candidate.variant_attributes["trustedReferenceSource"] == "wasPriceFromSavings.youSave"
    assert any("Walmart reference price source: wasPriceFromSavings.youSave" in signal for signal in candidate.signals)


def test_walmart_msrp_only_is_context_not_verified_markdown():
    install_walmart_savings_reference_patch()
    item = {
        "itemId": 12345,
        "name": "Gaming Monitor 27 inch 144Hz",
        "salePrice": 99.0,
        "msrp": 179.99,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=12345",
        "stock": "Available",
        "availableOnline": True,
    }

    candidate = WalmartProvider(configured=True)._candidate_from_item(item, ProviderScanRequest(source_key="walmart", query="monitor"))

    assert candidate is not None
    assert candidate.typical_price is None
    assert candidate.variant_attributes["referencePriceTrusted"] == "no"
    assert candidate.variant_attributes["referenceContextSource"] == "msrp"
    assert any("Walmart reference shown but not counted: msrp=$179.99" in signal for signal in candidate.signals)
