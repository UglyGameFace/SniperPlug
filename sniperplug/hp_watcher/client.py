from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import logging
import time
from typing import Any
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession
from curl_cffi.requests import exceptions as curl_exceptions

from sniperplug.hp_watcher.config import HPWatcherSettings


log = logging.getLogger("sniperplug.hp_watcher.http")


@dataclass(frozen=True)
class HTTPDocument:
    url: str
    status: int
    text: str
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


@dataclass(frozen=True)
class _TransportResponse:
    url: str
    status: int
    text: str
    headers: Mapping[str, str]


class HPStoreClient:
    """HP store HTTP client backed by a browser-impersonating transport.

    HP's Akamai edge silently drops requests from plain HTTP clients (the
    TLS handshake completes, then zero bytes ever come back), so this client
    uses curl_cffi with a real browser TLS/HTTP fingerprint instead of
    aiohttp. The public interface (async context manager, fetch_document,
    fetch_price_batch) and the retry/log behaviour are unchanged from the
    aiohttp implementation so service-level counters keep their meaning.
    """

    def __init__(self, settings: HPWatcherSettings):
        self.settings = settings
        self._session: AsyncSession | None = None
        self._semaphore = asyncio.Semaphore(settings.request_concurrency)

    async def __aenter__(self) -> "HPStoreClient":
        # The impersonation profile supplies a coherent browser identity
        # (TLS fingerprint plus matching User-Agent / client hints), so the
        # configured HP_WATCHER_USER_AGENT is deliberately NOT sent: mixing
        # a bot UA with a browser fingerprint is exactly the mismatch the
        # edge is gating on. The setting stays accepted for compatibility.
        self._session = AsyncSession(
            impersonate=self.settings.impersonate,
            headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "application/json,text/html,application/xml,text/xml;q=0.9,*/*;q=0.8",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_document(
        self,
        url: str,
        *,
        etag: str = "",
        last_modified: str = "",
        attempts: int = 3,
    ) -> HTTPDocument:
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        response = await self._request("GET", url, headers=headers, attempts=attempts)
        if response.status == 304:
            return HTTPDocument(
                url=response.url,
                status=304,
                text="",
                etag=response.headers.get("ETag", etag),
                last_modified=response.headers.get("Last-Modified", last_modified),
                not_modified=True,
            )
        if response.status != 200:
            log.warning(
                "HP document request returned non-success status=%s requested_url=%s final_url=%s",
                response.status,
                url,
                response.url,
            )
            raise RuntimeError(f"HP request returned HTTP {response.status} for {url}")
        return HTTPDocument(
            url=response.url,
            status=response.status,
            text=response.text,
            etag=response.headers.get("ETag", ""),
            last_modified=response.headers.get("Last-Modified", ""),
        )

    async def fetch_price_batch(
        self,
        catalog_entry_ids: list[str],
        *,
        cache_bust: bool = False,
    ) -> HTTPDocument:
        clean_ids = [str(value).strip() for value in catalog_entry_ids if str(value).strip().isdigit()]
        if not clean_ids:
            raise ValueError("HP price batch requires numeric catalog entry IDs")
        params = {
            "action": "cupis",
            "catalogId": "10051",
            "catentryId": ",".join(dict.fromkeys(clean_ids)),
            "langId": "-1",
            "modelId": "",
            "storeId": "10151",
        }
        if cache_bust:
            params["_"] = str(int(time.time() * 1000))
        separator = "&" if "?" in self.settings.price_endpoint_url else "?"
        url = f"{self.settings.price_endpoint_url}{separator}{urlencode(params)}"
        return await self.fetch_document(url)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        attempts: int,
    ) -> _TransportResponse:
        session = self._session
        if session is None:
            raise RuntimeError("HPStoreClient must be used as an async context manager")

        attempt_count = max(1, int(attempts))
        last_error: Exception | None = None
        for attempt in range(attempt_count):
            try:
                async with self._semaphore:
                    response = await session.request(
                        method,
                        url,
                        headers=headers,
                        timeout=self.settings.request_timeout_seconds,
                        allow_redirects=True,
                    )
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempt_count:
                    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                    await asyncio.sleep(max(retry_after, 0.8 * (2**attempt)))
                    continue
                return _TransportResponse(
                    url=str(response.url),
                    status=int(response.status_code),
                    text=response.text,
                    headers=response.headers,
                )
            except asyncio.CancelledError:
                raise
            except curl_exceptions.Timeout as error:
                # Keep the historical classification: the aiohttp transport
                # surfaced timeouts as TimeoutError in the exhausted-retries
                # log line, and watchers/alerts read that shape.
                last_error = TimeoutError(str(error))
                if attempt + 1 >= attempt_count:
                    break
                await asyncio.sleep(0.8 * (2**attempt))
            except curl_exceptions.RequestException as error:
                last_error = error
                if attempt + 1 >= attempt_count:
                    break
                await asyncio.sleep(0.8 * (2**attempt))

        log.warning(
            "HP transport request exhausted retries method=%s url=%s attempts=%s timeout_s=%.1f error_type=%s error=%s",
            method,
            url,
            attempt_count,
            self.settings.request_timeout_seconds,
            type(last_error).__name__ if last_error is not None else "unknown",
            str(last_error or "")[:240],
        )
        raise RuntimeError(f"HP request failed for {url}: {last_error}") from last_error


def _retry_after_seconds(value: Any) -> float:
    try:
        return max(0.0, min(60.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
