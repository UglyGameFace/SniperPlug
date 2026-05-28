import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.deal_quality import DealQualityBucket, classify_candidate, classify_card, quality_summary


def test_classify_candidate_verified_markdown():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Verified Item",
        product_url="https://www.walmart.com/ip/1",
        current_price=50.0,
        typical_price=100.0,
        variant_attributes={"referencePriceTrusted": "yes"},
    )

    quality = classify_candidate(candidate)

    assert quality.bucket == DealQualityBucket.VERIFIED_MARKDOWN
    assert quality.public_postable is True


def test_classify_candidate_flip_lead_not_public_postable():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Flip Item",
        product_url="https://www.walmart.com/ip/2",
        current_price=39.88,
        variant_attributes={"marketplaceCompPrice": "96.00"},
    )

    quality = classify_candidate(candidate)

    assert quality.bucket == DealQualityBucket.FLIP_LEAD
    assert quality.public_postable is False


def test_classify_candidate_variant_risk_wins_over_flip():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Risk Item",
        product_url="https://www.walmart.com/ip/3",
        option_mismatch_warning="Selected option does not match parent title",
        variant_attributes={"marketplaceCompPrice": "96.00"},
    )

    quality = classify_candidate(candidate)

    assert quality.bucket == DealQualityBucket.VARIANT_RISK


def test_classify_card_and_quality_summary():
    verified_embed = discord.Embed(title="Verified", description="Clean deal")
    verified = DealCard(embed=verified_embed, url="https://example.com/1", label="verified", discount=60, score=80)
    verified.should_alert = True

    flip = DealCard(
        embed=discord.Embed(title="Flip", description="Marketplace comp and Flip estimate available"),
        url="https://example.com/2",
        label="flip",
    )

    assert classify_card(verified).bucket == DealQualityBucket.VERIFIED_MARKDOWN
    assert classify_card(flip).bucket == DealQualityBucket.FLIP_LEAD
    assert "verified" in quality_summary([verified, flip])
    assert "flip" in quality_summary([verified, flip])
