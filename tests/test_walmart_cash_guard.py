from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers import walmart as walmart_provider_module
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.walmart_cash_guard import install_strict_walmart_cash_guard, strict_walmart_promotion_proof


# Install the runtime patch for direct provider-function tests. This mirrors bot startup.
install_strict_walmart_cash_guard()


def test_walmart_cash_not_shown_without_explicit_walmart_cash_label():
    item = {
        "itemId": 1,
        "name": "Normal Product",
        "salePrice": 20.00,
        "rewards": {"amount": 20000},
    }

    attrs = strict_walmart_promotion_proof(item)

    assert "walmartCashSavings" not in attrs


def test_walmart_cash_blocks_absurd_explicit_amount():
    item = {
        "itemId": 1,
        "name": "Normal Product",
        "salePrice": 20.00,
        "promotion": {
            "name": "Walmart Cash",
            "amount": 20000,
        },
    }

    attrs = strict_walmart_promotion_proof(item)

    assert "walmartCashSavings" not in attrs


def test_walmart_cash_allows_full_price_reward():
    item = {
        "itemId": 1,
        "name": "Price Glitch Product",
        "salePrice": 49.99,
        "promotion": {
            "name": "Walmart Cash",
            "amount": 49.99,
        },
    }

    attrs = strict_walmart_promotion_proof(item)

    assert attrs["walmartCashSavings"] == "49.99"


def test_walmart_cash_allows_small_real_reward():
    item = {
        "itemId": 1,
        "name": "Household Item",
        "salePrice": 24.99,
        "promo": {
            "description": "Earn Walmart Cash on this item",
            "rewardAmount": 5.00,
        },
    }

    attrs = strict_walmart_promotion_proof(item)

    assert attrs["walmartCashSavings"] == "5.00"


def test_walmart_cash_ignores_ids_and_years():
    item = {
        "itemId": 1,
        "name": "LEGO Set",
        "salePrice": 95.99,
        "promotion": {
            "name": "Walmart Cash",
            "campaignId": 202405271234,
            "year": 2026,
            "id": "20000",
        },
    }

    attrs = strict_walmart_promotion_proof(item)

    assert "walmartCashSavings" not in attrs


def test_provider_patch_stops_generic_reward_from_becoming_walmart_cash():
    item = {
        "itemId": 1,
        "name": "Generic Reward Product",
        "salePrice": 12.99,
        "reward": "Earn 20000 reward points",
    }

    attrs = walmart_provider_module._walmart_promotion_proof(item)

    assert "walmartCashSavings" not in attrs


def test_price_proof_ignores_suspicious_walmart_cash_value():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Bad Cash Product",
        product_url="https://www.walmart.com/ip/1",
        current_price=20.00,
        typical_price=None,
        variant_attributes={"walmartCashSavings": "20000.00"},
    )

    proof = verified_deal_value(candidate.to_normalized_deal())

    assert proof.effective_savings == 0.0
    assert "Ignored suspicious Walmart Cash value" in proof.effective_value_notes


def test_provider_candidate_only_sets_real_walmart_cash():
    provider = WalmartProvider(WalmartAffiliateConfig(enabled=True, consumer_id="cid", private_key_b64="fake"))
    item = {
        "itemId": 123,
        "name": "Walmart Cash Product",
        "salePrice": 39.99,
        "productTrackingUrl": "https://goto.walmart.com/c/123/568844/9383?prodsku=123",
        "promotion": {"name": "Walmart Cash", "amount": 39.99},
    }

    candidate = provider._candidate_from_item(item, request=ProviderScanRequest(source_key="walmart", query="cash"))

    assert candidate is not None
    assert candidate.variant_attributes["walmartCashSavings"] == "39.99"
    assert any("Walmart Cash detected: $39.99" in signal for signal in candidate.signals)
