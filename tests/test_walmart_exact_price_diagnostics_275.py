from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from sniperplug.providers.walmart import WalmartProvider, WalmartProviderError
from sniperplug.providers.walmart_exact_lookup import (
    EXACT_LOOKUP_PRICE_DIAGNOSTIC_MARKER,
    _fetch_exact_affiliate_product_lookup_payload_sync,
    summarize_product_lookup_price_shape,
)


class DiagnosticLookupFixture:
    detail_url = WalmartProvider.detail_url

    def __init__(self, payload: dict | None = None, *, error: Exception | None = None) -> None:
        self.payload = payload or {}
        self.error = error
        self.config = SimpleNamespace(publisher_id=None)
        self.urls: list[str] = []

    def _request_json(self, url: str) -> dict:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.payload


def test_price_shape_summary_exposes_unknown_price_paths_without_dumping_title() -> None:
    payload = {
        "itemId": 910206,
        "name": "Do not dump this product title into diagnostics",
        "sellerName": "Walmart",
        "selectedOffer": {
            "offerId": "offer-910206",
            "pricing": {
                "checkout": {"amount": 19.99},
                "comparison": {"originalAmount": 39.99},
            },
        },
    }

    summary = summarize_product_lookup_price_shape(payload, "910206")

    assert "selectedOffer.pricing.checkout.amount:float:19.99" in summary
    assert "selectedOffer.pricing.comparison.originalAmount:float:39.99" in summary
    assert "selectedOffer:dict[offerId,pricing]" in summary
    assert "Do_not_dump" not in summary
    assert len(summary) <= 320


def test_missing_known_current_price_logs_one_bounded_shape_sample(caplog) -> None:
    fixture = DiagnosticLookupFixture(
        {
            "items": [
                {
                    "itemId": 910207,
                    "name": "Unknown price shape item",
                    "sellerName": "Walmart",
                    "selectedOffer": {
                        "offerId": "offer-910207",
                        "pricing": {"checkout": {"amount": 24.99}},
                    },
                }
            ]
        }
    )
    caplog.set_level(logging.INFO, logger="sniperplug.autoscan.walmart_exact_lookup")

    first = _fetch_exact_affiliate_product_lookup_payload_sync(fixture, "910207")
    second = _fetch_exact_affiliate_product_lookup_payload_sync(fixture, "910207")

    assert first["itemId"] == 910207
    assert second["itemId"] == 910207
    messages = [
        record.getMessage()
        for record in caplog.records
        if "missing-current-price diagnostic" in record.getMessage()
    ]
    assert len(messages) == 1
    assert f"marker={EXACT_LOOKUP_PRICE_DIAGNOSTIC_MARKER}" in messages[0]
    assert "item_id=910207" in messages[0]
    assert "selectedOffer.pricing.checkout.amount:float:24.99" in messages[0]
    assert "Unknown price shape item" not in messages[0]


def test_known_current_price_does_not_emit_missing_price_diagnostic(caplog) -> None:
    fixture = DiagnosticLookupFixture(
        {
            "items": [
                {
                    "itemId": 910208,
                    "name": "Normal price item",
                    "salePrice": 18.0,
                    "sellerName": "Walmart",
                }
            ]
        }
    )
    caplog.set_level(logging.INFO, logger="sniperplug.autoscan.walmart_exact_lookup")

    payload = _fetch_exact_affiliate_product_lookup_payload_sync(fixture, "910208")

    assert payload["salePrice"] == 18.0
    assert not any(
        "missing-current-price diagnostic" in record.getMessage()
        for record in caplog.records
    )


def test_http_400_diagnostic_is_bounded_and_reraises(caplog) -> None:
    fixture = DiagnosticLookupFixture(
        error=WalmartProviderError(
            "Walmart API HTTP 400: exact lookup request rejected by upstream",
            retry_kind="api_http_400",
        )
    )
    caplog.set_level(logging.INFO, logger="sniperplug.autoscan.walmart_exact_lookup")

    with pytest.raises(WalmartProviderError):
        _fetch_exact_affiliate_product_lookup_payload_sync(fixture, "910209")
    with pytest.raises(WalmartProviderError):
        _fetch_exact_affiliate_product_lookup_payload_sync(fixture, "910209")

    messages = [
        record.getMessage()
        for record in caplog.records
        if "HTTP 400 diagnostic" in record.getMessage()
    ]
    assert len(messages) == 1
    assert f"marker={EXACT_LOOKUP_PRICE_DIAGNOSTIC_MARKER}" in messages[0]
    assert "item_id=910209" in messages[0]
