from pathlib import Path

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_cash_offers import (
    DEFAULT_CASH_QUERIES,
    build_walmart_cash_summary_embed,
    walmart_cash_search_terms,
)
from sniperplug.services.walmart_cash_pipeline import _strip_search_level_cash_attrs


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SOURCE = (ROOT / "sniperplug/services/walmart_cash_pipeline.py").read_text(encoding="utf-8")
OFFER_SOURCE = (ROOT / "sniperplug/services/walmart_cash_offers.py").read_text(encoding="utf-8")


def exact_candidate(*, item_id: str = "123456789") -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Exact Cash Test Product",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        product_id=item_id,
        product_id_type="item_id",
        current_price=20.00,
        variant_attributes={
            "walmartCashApiProof": "yes",
            "walmartCashProofMode": "strict_api_field_amount",
            "walmartCashAmount": "5.00",
            "walmartCashSavings": "5.00",
            "walmartCashProofPath": "items[0].manufacturerOffer.walmartCashAmount",
            "walmartCashProofText": "Earn $5 Walmart Cash",
            "walmartCashProofLabel": "Manufacturer offer Walmart Cash amount",
        },
        signals=["Walmart Cash API proof: $5.00"],
    )


def test_runtime_has_no_public_pdp_cash_fetch_path() -> None:
    assert "walmart_pdp_cash_proof" not in PIPELINE_SOURCE
    assert "check_walmart_pdp_cash_truth" not in PIPELINE_SOURCE
    assert "fetch_public_walmart_pdp_html" not in PIPELINE_SOURCE
    assert '"public_pdp_fallback": "disabled"' in PIPELINE_SOURCE


def test_broad_cash_routes_are_product_departments_not_promo_phrases() -> None:
    assert len(DEFAULT_CASH_QUERIES) >= 6
    assert all("walmart cash" not in query.lower() for query in DEFAULT_CASH_QUERIES)
    assert walmart_cash_search_terms("walmart cash") == DEFAULT_CASH_QUERIES
    assert walmart_cash_search_terms("tide walmart cash") == ("tide",)
    assert walmart_cash_search_terms("tide walmart cash offers") == ("tide",)


def test_exact_search_api_cash_proof_is_preserved() -> None:
    candidate = _strip_search_level_cash_attrs(exact_candidate())
    attrs = candidate.variant_attributes

    assert attrs["walmartCashApiProof"] == "yes"
    assert attrs["cashAmountConfirmed"] == "yes"
    assert attrs["cashProofSource"] == "affiliate_search"
    assert attrs["cashExactIdentityVerified"] == "yes"


def test_mismatched_product_url_cannot_preserve_search_cash_proof() -> None:
    candidate = exact_candidate(item_id="123456789")
    candidate.direct_product_url = "https://www.walmart.com/ip/987654321"
    candidate.product_url = candidate.direct_product_url

    sanitized = _strip_search_level_cash_attrs(candidate)
    assert "walmartCashApiProof" not in sanitized.variant_attributes
    assert sanitized.variant_attributes.get("cashAmountConfirmed") != "yes"


def test_deceptive_non_walmart_hosts_cannot_preserve_search_cash_proof() -> None:
    for deceptive_url in (
        "https://notwalmart.com/ip/123456789",
        "https://walmart.com.evil.com/ip/123456789",
    ):
        candidate = exact_candidate()
        candidate.direct_product_url = deceptive_url
        candidate.product_url = deceptive_url

        sanitized = _strip_search_level_cash_attrs(candidate)
        assert "walmartCashApiProof" not in sanitized.variant_attributes
        assert sanitized.variant_attributes.get("cashAmountConfirmed") != "yes"


def test_real_walmart_subdomain_still_preserves_search_cash_proof() -> None:
    candidate = exact_candidate()
    candidate.direct_product_url = "https://www.walmart.com/ip/Exact-Cash-Test/123456789"
    candidate.product_url = candidate.direct_product_url

    sanitized = _strip_search_level_cash_attrs(candidate)
    assert sanitized.variant_attributes["cashAmountConfirmed"] == "yes"


def test_normal_cash_summary_is_compact_hides_raw_diagnostics_and_keeps_api_failure() -> None:
    embed = build_walmart_cash_summary_embed(
        "walmart cash",
        ("personal care", "beauty"),
        checked=48,
        found=0,
        warnings=(
            "WALMART_PUBLISHER_ID is blank; using direct Walmart links.",
            "Exact Walmart PDP checked at https://www.walmart.com/ip/123; Robot or Human; html_chars=15190",
            "Walmart API HTTP 500: temporary failure",
        ),
        detail_checked=24,
        detail_unavailable=False,
        partial=False,
        capability_label="Signed Affiliate API configured",
        promo_counts={
            "cash_badge_seen": 2,
            "badge_rows_without_amount": 2,
            "detail_rows_attempted": 24,
            "confirmed_walmart_cash_amount_rows": 0,
        },
    )

    rendered = "\n".join(
        [embed.title or "", embed.description or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
        + [embed.footer.text or ""]
    )
    lowered = rendered.lower()

    assert len(embed.fields) <= 3
    assert len(rendered) < 1800
    assert "official walmart api only" in lowered
    assert "no api-proven walmart cash in this scan" in lowered
    assert "official walmart api request failed (http 500)" in lowered
    assert "temporary failure" not in lowered
    assert "robot or human" not in lowered
    assert "html_chars" not in lowered
    assert "https://www.walmart.com/ip/123" not in rendered
    assert "search routes actually checked" not in lowered
    assert "other promo types seen separately" not in lowered


def test_offer_copy_never_advertises_api_pdp_hybrid_proof() -> None:
    assert "API/PDP-proven" not in OFFER_SOURCE
    assert "Walmart PDP fallback" not in OFFER_SOURCE
    assert "public PDP scraping disabled" in OFFER_SOURCE
