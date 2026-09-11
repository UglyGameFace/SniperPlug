from __future__ import annotations

import gzip
import io
import json
import logging
import urllib.error
import urllib.parse
from types import SimpleNamespace

import pytest

from sniperplug.providers.walmart import WalmartProvider, WalmartProviderError
from sniperplug.providers import walmart_exact_lookup as exact_lookup
from sniperplug.providers.walmart_exact_lookup import (
    EXACT_LOOKUP_HTTP_ERROR_DECODE_MARKER,
    _decode_http_error_body,
    _fetch_exact_affiliate_product_lookup_payload_sync,
)


class ExactTransportFixture:
    detail_url = WalmartProvider.detail_url

    def __init__(self) -> None:
        self.config = SimpleNamespace(publisher_id=None, timeout_seconds=12)
        self.signed_header_calls = 0
        self.generic_request_calls = 0

    def _signed_headers(self) -> dict[str, str]:
        self.signed_header_calls += 1
        return {"Accept": "application/json"}

    def _request_json(self, _url: str) -> dict:
        self.generic_request_calls += 1
        raise AssertionError("real exact transport should not use generic _request_json")


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._stream = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._stream.read()

    def close(self) -> None:
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def test_exact_lookup_real_transport_preserves_exact_ids_request(monkeypatch) -> None:
    provider = ExactTransportFixture()
    seen: dict[str, object] = {}

    def fake_urlopen(request, *, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse(
            {
                "items": [
                    {
                        "itemId": 910286,
                        "name": "Exact transport fixture",
                        "salePrice": 19.99,
                    }
                ]
            }
        )

    monkeypatch.setattr(exact_lookup.urllib.request, "urlopen", fake_urlopen)

    payload = _fetch_exact_affiliate_product_lookup_payload_sync(provider, "910286")

    query = urllib.parse.parse_qs(urllib.parse.urlparse(str(seen["url"])).query)
    assert query["ids"] == ["910286"]
    assert query["responseGroup"] == ["full"]
    assert seen["timeout"] == 12
    assert provider.signed_header_calls == 1
    assert provider.generic_request_calls == 0
    assert payload["itemId"] == 910286


def test_gzip_http_400_is_decoded_closed_and_logged_readably(monkeypatch, caplog) -> None:
    provider = ExactTransportFixture()
    body_stream = io.BytesIO(
        gzip.compress(
            json.dumps(
                {"errors": [{"code": 4008, "message": "Missing itemId"}]}
            ).encode("utf-8")
        )
    )
    http_error = urllib.error.HTTPError(
        "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/items",
        400,
        "Bad Request",
        {"Content-Encoding": "gzip"},
        body_stream,
    )

    def fake_urlopen(_request, *, timeout):
        assert timeout == 12
        raise http_error

    monkeypatch.setattr(exact_lookup.urllib.request, "urlopen", fake_urlopen)
    caplog.set_level(logging.INFO, logger="sniperplug.autoscan.walmart_exact_lookup")

    with pytest.raises(WalmartProviderError) as raised:
        _fetch_exact_affiliate_product_lookup_payload_sync(provider, "910287")

    assert raised.value.retry_kind == "api_http_400"
    assert "Missing itemId" in str(raised.value)
    assert "4008" in str(raised.value)
    assert body_stream.closed is True

    messages = [
        record.getMessage()
        for record in caplog.records
        if "HTTP 400 diagnostic" in record.getMessage()
    ]
    assert len(messages) == 1
    assert f"decode={EXACT_LOOKUP_HTTP_ERROR_DECODE_MARKER}" in messages[0]
    assert "Missing_itemId" in messages[0]
    assert "4008" in messages[0]


def test_http_error_decoder_falls_back_without_corrupting_plain_json() -> None:
    raw = b'{"errors":[{"message":"plain upstream validation error"}]}'

    assert _decode_http_error_body(raw, content_encoding="") == raw.decode("utf-8")
    assert _decode_http_error_body(raw, content_encoding="gzip") == raw.decode(
        "utf-8"
    )
