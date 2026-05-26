from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.walmart_card_renderer import api_evidence_lines, price_block


def test_price_block_labels_untrusted_reference_without_guessing_discount():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="API item",
        product_url="https://www.walmart.com/ip/1",
        current_price=10.0,
        typical_price=None,
        variant_attributes={
            "referencePriceTrusted": "no",
            "referenceContextPrice": "20.00",
            "referenceContextSource": "listPrice",
        },
    )
    deal = candidate.to_normalized_deal()
    rendered = price_block(deal, verified_deal_value(deal))

    assert "Reference shown: **$20.00** `listPrice`" in rendered
    assert "not counted for % off" in rendered
    assert "API-derived save" not in rendered


def test_api_evidence_lines_use_candidate_signals_only():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="API item",
        product_url="https://www.walmart.com/ip/2",
        current_price=10.0,
        typical_price=20.0,
        signals=(
            "Walmart current price source: salePrice",
            "Walmart reference price source: wasPrice",
        ),
    )
    deal = candidate.to_normalized_deal()
    lines = api_evidence_lines(candidate, deal, verified_deal_value(deal))
    rendered = "\n".join(lines)

    assert "salePrice" in rendered
    assert "wasPrice" in rendered
