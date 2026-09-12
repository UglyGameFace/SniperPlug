from __future__ import annotations

import gzip
import io
import json
import logging
import urllib.error
from types import SimpleNamespace

import pytest

from sniperplug.providers import walmart_exact_lookup as exact_lookup
from sniperplug.providers.walmart import WalmartProvider, WalmartProviderError
from sniperplug.providers.walmart_exact_lookup import (
    EXACT_LOOKUP_HTTP_ERROR_DECODE_MARKER,
    EXACT_LOOKUP_RESULTS_NOT_FOUND_MARKER,
    _fetch_exact_affiliate_product_lookup_payload_sync,
    _is_affiliate_results_not_found,
)


class ExactTransportFixture:
    detail_url = WalmartProvider.detail_url

    def __init__(self) -> None:
        self.config = SimpleNamespace(publisher_id=None, timeout_seconds=12)

    def _signed_headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    def _request_json(self, _url: str) -> dict:
        raise AssertionError("real exact transport should not use generic _request_json")


def _http_error(body: dict, *, encoding: str = "gzip") -> tuple[urllib.error.HTTPError, io.BytesIO]:
    raw = json.dumps(body).encode("utf-8")
    if encoding == "gzip":
        raw = gzip.compress(raw)
    stream = io.BytesIO(raw)
    return (
        urllib.error.HTTPError(
            "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/items",
            400,
            "Bad Request",
            {"Content-Encoding": encoding},
            stream,
        ),
        stream,
    )


def test_http_400_code_6001_results_not_found_normalizes_to_exact_no_match(
    monkeypatch,
    caplog,
) -> None:
    provider = ExactTransportFixture()
    http_error, body_stream = _http_error(
        {"errors": [{"code": 6001, "message": "Results not found"}]}
    )

    def fake_urlopen(_request, *, timeout):
        assert timeout == 12
        raise http_error

    monkeypatch.setattr(exact_lookup.urllib.request, "urlopen", fake_urlopen)
    caplog.set_level(logging.INFO, logger="sniperplug.autoscan.walmart_exact_lookup")

    with pytest.raises(WalmartProviderError) as raised:
        _fetch_exact_affiliate_product_lookup_payload_sync(provider, "611311887")

    assert raised.value.retry_kind == "api_exact_lookup_no_match"
    assert (
        getattr(raised.value, "exact_lookup_normalization_marker", "")
        == EXACT_LOOKUP_RESULTS_NOT_FOUND_MARKER
    )
    assert "6001" in str(raised.value)
    assert "Results not found" in str(raised.value)
    assert body_stream.closed is True

    messages = [record.getMessage() for record in caplog.records]
    assert any(EXACT_LOOKUP_RESULTS_NOT_FOUND_MARKER in message for message in messages)
    assert any(EXACT_LOOKUP_HTTP_ERROR_DECODE_MARKER in message for message in messages)
    assert not any("HTTP 400 diagnostic" in message for message in messages)


def test_only_exact_structured_6001_results_not_found_shape_is_normalized() -> None:
    assert _is_affiliate_results_not_found(
        json.dumps({"errors": [{"code": "6001", "message": " Results   not found "}]})
    )
    assert not _is_affiliate_results_not_found(
        json.dumps({"errors": [{"code": 4008, "message": "Missing itemId"}]})
    )
    assert not _is_affiliate_results_not_found(
        json.dumps(
            {
                "errors": [
                    {"code": 6001, "message": "Results not found"},
                    {"code": 4008, "message": "Missing itemId"},
                ]
            }
        )
    )
    assert not _is_affiliate_results_not_found("not json")


def test_other_http_400_still_uses_generic_http_retry_kind(monkeypatch) -> None:
    provider = ExactTransportFixture()
    http_error, body_stream = _http_error(
        {"errors": [{"code": 4008, "message": "Missing itemId"}]},
        encoding="",
    )

    def fake_urlopen(_request, *, timeout):
        assert timeout == 12
        raise http_error

    monkeypatch.setattr(exact_lookup.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(WalmartProviderError) as raised:
        _fetch_exact_affiliate_product_lookup_payload_sync(provider, "910289")

    assert raised.value.retry_kind == "api_http_400"
    assert "4008" in str(raised.value)
    assert "Missing itemId" in str(raised.value)
    assert body_stream.closed is True
