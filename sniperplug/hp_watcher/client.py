from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any
from urllib.parse import urlencode

import aiohttp

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


class HPStoreClient:
    def __init__(self, settings: HPWatcherSettings):
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(settings.request_concurrency)

    async def __aenter__(self) -> "HPStoreClient":
        timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "application/json,text/html,application/xml,text/xml;q=0.9,*/*;q=0.8",
            },
            raise_for_status=False,
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
        async with response:
            if response.status == 304:
                return HTTPDocument(
                    url=str(response.url),
                    status=304,
                    text="",
                    etag=response.headers.get("ETag", etag),
                    last_modified=response.headers.get("Last-Modified", last_modified),
                    not_modified=True,
                )
            text = await response.text(errors="replace")
            if response.status != 200:
                raise RuntimeError(f"HP request returned HTTP {response.status} for {url}")
            return HTTPDocument(
                url=str(response.url),
                status=response.status,
                text=text,
                etag=response.headers.get("ETag", ""),
                last_modified=response.headers.get("Last-Modified", ""),
            )

    async def fetch_price_batch(self, catalog_entry_ids: list[str]) -> HTTPDocument:
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
    ) -> aiohttp.ClientResponse:
        session = self._session
        if session is None:
            raise RuntimeError("HPStoreClient must be used as an async context manager")

        last_error: Exception | None = None
        for attempt in range(max(1, int(attempts))):
            try:
                async with self._semaphore:
                    response = await session.request(
                        method,
                        url,
                        headers=headers,
                        allow_redirects=True,
                    )
                if response.status in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                    response.release()
                    await asyncio.sleep(max(retry_after, 0.8 * (2**attempt)))
                    continue
                return response
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_error = error
                if attempt + 1 >= attempts:
                    break
                await asyncio.sleep(0.8 * (2**attempt))
        raise RuntimeError(f"HP request failed for {url}: {last_error}") from last_error


def _retry_after_seconds(value: Any) -> float:
    try:
        return max(0.0, min(60.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
