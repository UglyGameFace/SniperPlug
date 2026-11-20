from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

import pytest
from curl_cffi.requests.exceptions import Timeout as CurlTimeout

from sniperplug.hp_watcher import client as client_module
from sniperplug.hp_watcher.client import HPStoreClient
from sniperplug.hp_watcher.config import HPWatcherSettings


PRODUCT_URL = "https://www.hp.com/us-en/shop/pdp/hp-travel-backpack"


class FakeResponse:
    def __init__(self, *, status_code=200, url="", text="", headers=None):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.headers = dict(headers or {})


class FakeSession:
    """Stand-in for curl_cffi's AsyncSession with a scripted response queue."""

    script: ClassVar[list] = []
    instances: ClassVar[list] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls: list = []
        self.closed = False
        FakeSession.instances.append(self)

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = FakeSession.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if not item.url:
            item.url = url
        return item

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_transport(monkeypatch):
    FakeSession.script = []
    FakeSession.instances = []
    monkeypatch.setattr(client_module, "AsyncSession", FakeSession)
    return FakeSession


def test_fetch_document_success_uses_browser_impersonation(fake_transport) -> None:
    async def run() -> None:
        fake_transport.script = [
            FakeResponse(
                status_code=200,
                text="<html>hp product</html>",
                headers={"ETag": "v1", "Last-Modified": "Wed, 01 Jan 2026 00:00:00 GMT"},
            )
        ]
        settings = HPWatcherSettings()
        async with HPStoreClient(settings) as client:
            document = await client.fetch_document(PRODUCT_URL)

        assert document.status == 200
        assert document.text == "<html>hp product</html>"
        assert document.url == PRODUCT_URL
        assert document.etag == "v1"
        assert document.last_modified == "Wed, 01 Jan 2026 00:00:00 GMT"
        assert document.not_modified is False

        session = fake_transport.instances[-1]
        assert session.kwargs["impersonate"] == "chrome"
        assert "User-Agent" not in session.kwargs.get("headers", {})
        method, url, kwargs = session.calls[0]
        assert method == "GET"
        assert url == PRODUCT_URL
        assert kwargs["timeout"] == settings.request_timeout_seconds
        assert kwargs["allow_redirects"] is True
        assert session.closed is True

    asyncio.run(run())


def test_fetch_document_uses_configured_impersonate_profile(fake_transport) -> None:
    async def run() -> None:
        fake_transport.script = [FakeResponse(status_code=200, text="ok")]
        settings = HPWatcherSettings(impersonate="chrome124")
        async with HPStoreClient(settings) as client:
            await client.fetch_document(PRODUCT_URL)
        assert fake_transport.instances[-1].kwargs["impersonate"] == "chrome124"

    asyncio.run(run())


def test_fetch_document_not_modified(fake_transport) -> None:
    async def run() -> None:
        fake_transport.script = [FakeResponse(status_code=304)]
        async with HPStoreClient(HPWatcherSettings()) as client:
            document = await client.fetch_document(
                PRODUCT_URL,
                etag="old-etag",
                last_modified="Tue, 01 Dec 2026 00:00:00 GMT",
            )
        assert document.status == 304
        assert document.not_modified is True
        assert document.text == ""
        assert document.etag == "old-etag"
        assert document.last_modified == "Tue, 01 Dec 2026 00:00:00 GMT"
        _, _, kwargs = fake_transport.instances[-1].calls[0]
        assert kwargs["headers"]["If-None-Match"] == "old-etag"
        assert kwargs["headers"]["If-Modified-Since"] == "Tue, 01 Dec 2026 00:00:00 GMT"

    asyncio.run(run())


def test_fetch_document_non_success_status_raises_and_logs(fake_transport, caplog) -> None:
    async def run() -> None:
        fake_transport.script = [FakeResponse(status_code=403, text="blocked")]
        async with HPStoreClient(HPWatcherSettings()) as client:
            with pytest.raises(RuntimeError, match="HTTP 403"):
                await client.fetch_document(PRODUCT_URL)

    with caplog.at_level(logging.WARNING, logger="sniperplug.hp_watcher.http"):
        asyncio.run(run())
    assert "HP document request returned non-success" in caplog.text
    assert "status=403" in caplog.text
    assert f"requested_url={PRODUCT_URL}" in caplog.text


def test_retryable_status_is_retried_then_succeeds(fake_transport) -> None:
    async def run() -> None:
        fake_transport.script = [
            FakeResponse(status_code=503, text="busy"),
            FakeResponse(status_code=200, text="recovered"),
        ]
        async with HPStoreClient(HPWatcherSettings()) as client:
            document = await client.fetch_document(PRODUCT_URL)
        assert document.status == 200
        assert document.text == "recovered"
        assert len(fake_transport.instances[-1].calls) == 2

    asyncio.run(run())


def test_timeout_is_retried_then_recovers(fake_transport) -> None:
    async def run() -> None:
        fake_transport.script = [
            CurlTimeout("operation timed out"),
            FakeResponse(status_code=200, text="recovered"),
        ]
        async with HPStoreClient(HPWatcherSettings()) as client:
            document = await client.fetch_document(PRODUCT_URL)
        assert document.status == 200
        assert len(fake_transport.instances[-1].calls) == 2

    asyncio.run(run())


def test_timeout_exhausts_retries_with_same_log_shape(fake_transport, caplog) -> None:
    async def run() -> None:
        fake_transport.script = [
            CurlTimeout("operation timed out"),
            CurlTimeout("operation timed out"),
        ]
        async with HPStoreClient(HPWatcherSettings()) as client:
            with pytest.raises(RuntimeError, match="HP request failed"):
                await client.fetch_document(PRODUCT_URL, attempts=2)

    with caplog.at_level(logging.WARNING, logger="sniperplug.hp_watcher.http"):
        asyncio.run(run())
    assert len(fake_transport.instances[-1].calls) == 2
    assert "HP transport request exhausted retries" in caplog.text
    assert "method=GET" in caplog.text
    assert f"url={PRODUCT_URL}" in caplog.text
    assert "attempts=2" in caplog.text
    assert "timeout_s=20.0" in caplog.text
    assert "error_type=TimeoutError" in caplog.text


def test_fetch_price_batch_builds_services_url(fake_transport) -> None:
    async def run() -> None:
        fake_transport.script = [FakeResponse(status_code=200, text="{}")]
        settings = HPWatcherSettings()
        async with HPStoreClient(settings) as client:
            document = await client.fetch_price_batch(["123", "0456", "abc", "123"])
        assert document.status == 200
        _, url, _ = fake_transport.instances[-1].calls[0]
        assert url.startswith(settings.price_endpoint_url + "?")
        assert "action=cupis" in url
        assert "catentryId=123%2C0456" in url

    asyncio.run(run())


def test_client_requires_async_context_manager() -> None:
    async def run() -> None:
        client = HPStoreClient(HPWatcherSettings())
        with pytest.raises(RuntimeError, match="async context manager"):
            await client.fetch_document(PRODUCT_URL)

    asyncio.run(run())
