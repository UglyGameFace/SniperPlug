from __future__ import annotations

import io
import urllib.error

import pytest

from sniperplug.providers import walmart_page_recovery as page_recovery


def _http_error(code: int) -> tuple[urllib.error.HTTPError, io.BytesIO]:
    body = io.BytesIO(f"HTTP {code}".encode("utf-8"))
    error = urllib.error.HTTPError(
        "https://www.walmart.com/ip/123456789",
        code,
        "fixture",
        {},
        body,
    )
    return error, body


def test_http_410_gone_is_page_absence_and_closes_response(monkeypatch) -> None:
    error, body = _http_error(410)

    def fake_urlopen(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(page_recovery.urllib.request, "urlopen", fake_urlopen)

    assert page_recovery._request_product_page_scripts("123456789", 3.0) == []
    assert body.closed is True


def test_http_404_absence_still_closes_response(monkeypatch) -> None:
    error, body = _http_error(404)

    def fake_urlopen(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(page_recovery.urllib.request, "urlopen", fake_urlopen)

    assert page_recovery._request_product_page_scripts("123456789", 3.0) == []
    assert body.closed is True


def test_transient_http_error_stays_retryable_and_closes_response(monkeypatch) -> None:
    error, body = _http_error(503)

    def fake_urlopen(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(page_recovery.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(page_recovery.WalmartPageRecoveryTransientError) as raised:
        page_recovery._request_product_page_scripts("123456789", 3.0)

    assert raised.value.reason_code == "page_http_503"
    assert body.closed is True


def test_gone_page_absence_remains_fail_closed_without_fabricating_proof() -> None:
    recovery = page_recovery.recover_page_identity_from_scripts(
        [],
        item_id="123456789",
        api_item_price=19.99,
    )

    assert recovery.proof is None
    assert recovery.status == "no_structured_data"
