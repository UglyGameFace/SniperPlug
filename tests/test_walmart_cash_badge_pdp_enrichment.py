from __future__ import annotations

import pytest

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_cash_offers import build_walmart_cash_summary_embed
from sniperplug.services.walmart_cash_pipeline import (
    detect_confirmed_walmart_cash_amount,
    detect_walmart_cash_badge,
    run_walmart_cash_discovery,
)


class FakeConfig:
    enabled = True
    consumer_id = "cid"
    timeout_seconds = 12


class FakeProvider:
    config = FakeConfig()

    def __init__(self, candidates, details):
        self._candidates = tuple(candidates)
        self._details = dict(details)
        self.detail_attempts = []

    async def scan(self, request):
        class Result:
            candidates = self._candidates
            warnings = ()
        return Result()

    async def fetch_product_detail_payload(self, item_id: str):
        self.detail_attempts.append(item_id)
        return self._details[item_id]


def candidate(**kwargs) -> SourceCandidate:
    base = {
        "source_key": "walmart",
        "retailer": "Walmart",
        "title": "Glad ForceFlex Trash Bags",
        "product_url": "https://www.walmart.com/ip/123",
        "current_price": 5.92,
        "product_id": "123",
        "sku": "123",
    }
    base.update(kwargs)
    return SourceCandidate(**base)


def test_search_row_badge_creates_private_candidate_state():
    row = candidate(variant_attributes={"badge": "Walmart Cash available"})
    badge = detect_walmart_cash_badge(row)
    assert badge is not None
    assert badge.proof_path == "variant_attributes.badge"


@pytest.mark.asyncio
async def test_badge_only_row_does_not_count_as_confirmed_offer(monkeypatch):
    monkeypatch.setenv("WALMART_OAUTH_ACCESS_TOKEN", "test-token")
    row = candidate(variant_attributes={"badge": "Walmart Cash available"})
    provider = FakeProvider([row], {"123": {"itemId": "123", "name": "Glad ForceFlex Trash Bags", "badges": [{"text": "Walmart Cash available"}]}})

    result = await run_walmart_cash_discovery(provider, search="detergent walmart cash", max_results=4, requested_by="tester")

    assert result.search_rows_checked == 1
    assert result.cash_badges_seen == 1
    assert result.detail_rows_attempted == 1
    assert result.detail_rows_checked == 1
    assert result.confirmed_cash_amount_rows == 0
    assert result.badge_rows_without_amount == 1
    assert result.cash_candidates == ()
    assert provider.detail_attempts == ["123"]


@pytest.mark.asyncio
async def test_detail_row_with_exact_amount_confirms_offer(monkeypatch):
    monkeypatch.setenv("WALMART_OAUTH_ACCESS_TOKEN", "test-token")
    row = candidate(variant_attributes={"badge": "Walmart Cash available"})
    provider = FakeProvider([row], {"123": {"itemId": "123", "name": "Glad ForceFlex Trash Bags", "promo": {"headline": "Earn $5 Walmart Cash"}}})

    result = await run_walmart_cash_discovery(provider, search="detergent walmart cash", max_results=4, requested_by="tester")

    assert result.cash_badges_seen == 1
    assert result.confirmed_cash_amount_rows == 1
    assert len(result.cash_candidates) == 1
    assert result.cash_candidates[0].variant_attributes["walmartCashApiProof"] == "yes"


def test_nested_detail_promo_object_with_amount_confirms_offer():
    item = {"itemId": "123", "offers": [{"type": "Walmart Cash", "amount": 3.0, "description": "Walmart Cash reward"}]}
    assert detect_confirmed_walmart_cash_amount(item, current_price=9.99)


def test_user_query_or_plain_title_does_not_prove_badge():
    row = candidate(title="Walmart Cash detergent")
    assert detect_walmart_cash_badge(row) is None


def test_summary_embed_reports_badges_separate_from_confirmed_amounts():
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
            "confirmed_walmart_cash_amount_rows": 0,
            "badge_rows_without_amount": 2,
        },
    )
    rendered = str(embed.to_dict())
    assert "Cash badges seen" in rendered
    assert "Walmart Cash badge seen on" in rendered
    assert "not shown as buy-worthy Cash offers yet" in rendered
    assert "Cash Finder does not public-post markdown alerts" in rendered
