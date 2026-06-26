from __future__ import annotations

import pytest

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_cash_offers import build_walmart_cash_summary_embed
from sniperplug.services.walmart_cash_pipeline import (
    detect_confirmed_walmart_cash_amount,
    detect_walmart_cash_badge,
    run_walmart_cash_discovery,
)
from sniperplug.services.walmart_pdp_cash_proof import extract_walmart_cash_from_pdp_html


class FakeConfig:
    enabled = True
    consumer_id = "cid"
    timeout_seconds = 12


class FakeProvider:
    config = FakeConfig()

    def __init__(self, candidates, details, pdp=None):
        self._candidates = tuple(candidates)
        self._details = dict(details)
        self._pdp = dict(pdp or {})
        self.detail_attempts = []
        self.pdp_attempts = []

    async def scan(self, request):
        class Result:
            candidates = self._candidates
            warnings = ()
        return Result()

    async def fetch_product_detail_payload(self, item_id: str):
        self.detail_attempts.append(item_id)
        return self._details[item_id]

    async def fetch_walmart_pdp_html(self, url: str):
        self.pdp_attempts.append(url)
        return self._pdp[url]


def candidate(**kwargs) -> SourceCandidate:
    base = {
        "source_key": "walmart",
        "retailer": "Walmart",
        "title": "Glad ForceFlex Trash Bags",
        "product_url": "https://www.walmart.com/ip/123",
        "direct_product_url": "https://www.walmart.com/ip/123",
        "current_price": 5.92,
        "product_id": "123",
        "sku": "123",
    }
    base.update(kwargs)
    return SourceCandidate(**base)


@pytest.mark.asyncio
async def test_badge_missing_still_checks_exact_pdp_and_confirms(monkeypatch):
    monkeypatch.setenv("WALMART_OAUTH_ACCESS_TOKEN", "test-token")
    row = candidate(variant_attributes={"clearance": "clearance"})
    provider = FakeProvider(
        [row],
        {"123": {"itemId": "123", "name": "Glad ForceFlex Trash Bags", "clearance": True}},
        {"https://www.walmart.com/ip/123": "<html><body>Earn $4 Walmart Cash on this exact product.</body></html>"},
    )

    result = await run_walmart_cash_discovery(provider, search="trash bags walmart cash", max_results=4, requested_by="tester")

    assert result.cash_badges_seen == 0
    assert result.detail_rows_checked == 1
    assert result.pdp_fallback_attempted == 1
    assert result.pdp_fallback_checked == 1
    assert result.pdp_cash_wording_seen == 1
    assert result.confirmed_cash_amount_rows == 1
    assert len(result.cash_candidates) == 1
    attrs = result.cash_candidates[0].variant_attributes
    assert attrs["cashProofSource"] == "walmart_pdp"
    assert attrs["walmartCashAmount"] == "4.00"


def test_search_row_badge_creates_private_candidate_state():
    row = candidate(variant_attributes={"badge": "Walmart Cash available"})
    badge = detect_walmart_cash_badge(row)
    assert badge is not None
    assert badge.proof_path == "variant_attributes.badge"


@pytest.mark.asyncio
async def test_badge_only_row_does_not_count_as_confirmed_offer(monkeypatch):
    monkeypatch.setenv("WALMART_OAUTH_ACCESS_TOKEN", "test-token")
    row = candidate(variant_attributes={"badge": "Walmart Cash available"})
    provider = FakeProvider(
        [row],
        {"123": {"itemId": "123", "name": "Glad ForceFlex Trash Bags", "badges": [{"text": "Walmart Cash available"}]}},
        {"https://www.walmart.com/ip/123": "<html><body>Walmart Cash available</body></html>"},
    )

    result = await run_walmart_cash_discovery(provider, search="detergent walmart cash", max_results=4, requested_by="tester")

    assert result.search_rows_checked == 1
    assert result.cash_badges_seen == 1
    assert result.detail_rows_attempted == 1
    assert result.detail_rows_checked == 1
    assert result.pdp_fallback_attempted == 1
    assert result.pdp_fallback_checked == 1
    assert result.pdp_cash_wording_seen == 1
    assert result.confirmed_cash_amount_rows == 0
    assert result.badge_rows_without_amount == 1
    assert result.cash_candidates == ()
    assert provider.detail_attempts == ["123"]
    assert provider.pdp_attempts == ["https://www.walmart.com/ip/123"]


@pytest.mark.asyncio
async def test_detail_row_with_exact_amount_confirms_offer(monkeypatch):
    monkeypatch.setenv("WALMART_OAUTH_ACCESS_TOKEN", "test-token")
    row = candidate(variant_attributes={"badge": "Walmart Cash available"})
    provider = FakeProvider([row], {"123": {"itemId": "123", "name": "Glad ForceFlex Trash Bags", "promo": {"headline": "Earn $5 Walmart Cash"}}})

    result = await run_walmart_cash_discovery(provider, search="detergent walmart cash", max_results=4, requested_by="tester")

    assert result.cash_badges_seen == 1
    assert result.confirmed_cash_amount_rows == 1
    assert result.pdp_fallback_attempted == 0
    assert len(result.cash_candidates) == 1
    assert result.cash_candidates[0].variant_attributes["walmartCashApiProof"] == "yes"
    assert result.cash_candidates[0].variant_attributes["cashProofSource"] == "affiliate_detail"


@pytest.mark.asyncio
async def test_affiliate_detail_without_amount_falls_back_to_exact_pdp_url(monkeypatch):
    monkeypatch.setenv("WALMART_OAUTH_ACCESS_TOKEN", "test-token")
    row = candidate(variant_attributes={"badge": "Walmart Cash available"})
    provider = FakeProvider(
        [row],
        {"123": {"itemId": "123", "name": "Glad ForceFlex Trash Bags", "badges": [{"text": "Walmart Cash available"}]}},
        {"https://www.walmart.com/ip/123": "<html><body>Earn $5 Walmart Cash on this exact product.</body></html>"},
    )

    result = await run_walmart_cash_discovery(provider, search="walmart cash trash bags", max_results=4, requested_by="tester")

    assert provider.pdp_attempts == ["https://www.walmart.com/ip/123"]
    assert result.pdp_fallback_attempted == 1
    assert result.pdp_fallback_checked == 1
    assert result.pdp_cash_wording_seen == 1
    assert result.confirmed_cash_amount_rows == 1
    assert len(result.cash_candidates) == 1
    attrs = result.cash_candidates[0].variant_attributes
    assert attrs["cashProofSource"] == "walmart_pdp"
    assert attrs["cashDetailUrl"] == "https://www.walmart.com/ip/123"
    assert attrs["walmartCashAmount"] == "5.00"


def test_pdp_text_walmart_cash_amount_confirms_offer():
    proof = extract_walmart_cash_from_pdp_html("<html><body>Earn $5 Walmart Cash with this product.</body></html>", current_price=20.0)
    assert proof is not None
    assert proof.amount == 5.0
    assert proof.proof_path.startswith("walmart_pdp.text")


def test_pdp_embedded_json_walmart_cash_amount_confirms_offer():
    html = """
    <script type="application/json">
    {"product":{"promo":{"type":"Walmart Cash","amount":3.0,"description":"Walmart Cash reward"}}}
    </script>
    """
    proof = extract_walmart_cash_from_pdp_html(html, current_price=20.0)
    assert proof is not None
    assert proof.amount == 3.0
    assert "script_json" in proof.proof_path


def test_pdp_walmart_cash_available_without_amount_does_not_confirm():
    proof = extract_walmart_cash_from_pdp_html("<html><body>Walmart Cash available</body></html>", current_price=20.0)
    assert proof is None


def test_pdp_onepay_cashback_does_not_confirm_walmart_cash():
    proof = extract_walmart_cash_from_pdp_html("<html><body>OnePay cashback $5 available</body></html>", current_price=20.0)
    assert proof is None


def test_pdp_generic_cashback_does_not_confirm_walmart_cash():
    proof = extract_walmart_cash_from_pdp_html("<html><body>Get $5 cashback on this order</body></html>", current_price=20.0)
    assert proof is None


def test_nested_detail_promo_object_with_amount_confirms_offer():
    item = {"itemId": "123", "offers": [{"type": "Walmart Cash", "amount": 3.0, "description": "Walmart Cash reward"}]}
    assert detect_confirmed_walmart_cash_amount(item, current_price=9.99)


def test_user_query_or_plain_title_does_not_prove_badge():
    row = candidate(title="Walmart Cash detergent")
    assert detect_walmart_cash_badge(row) is None


def test_summary_embed_reports_pdp_badges_separate_from_confirmed_amounts():
    embed = build_walmart_cash_summary_embed(
        "detergent",
        ("detergent", "detergent walmart cash"),
        checked=2,
        found=0,
        warnings=(),
        detail_checked=2,
        promo_counts={
            "cash_badge_seen": 2,
            "detail_rows_attempted": 2,
            "detail_rows_checked": 2,
            "pdp_fallback_attempted": 2,
            "pdp_fallback_checked": 2,
            "pdp_cash_wording_seen": 1,
            "confirmed_walmart_cash_amount_rows": 0,
            "badge_rows_without_amount": 2,
            "clearance": 1,
        },
    )
    rendered = str(embed.to_dict())
    assert "Cash badges seen" in rendered
    assert "Exact Walmart PDP fallback checked" in rendered
    assert "Walmart Cash wording found, but no dollar amount was exposed" in rendered
    assert "Clearance signal" in rendered
    assert "Cash Finder does not public-post markdown/open-box alerts" in rendered
