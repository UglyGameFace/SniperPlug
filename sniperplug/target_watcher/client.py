from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp

from sniperplug.target_watcher.config import TargetWatcherSettings


@dataclass(frozen=True)
class TargetBinaryDocument:
    url: str
    status: int
    body: bytes
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


@dataclass(frozen=True)
class TargetJSONDocument:
    url: str
    status: int
    payload: dict[str, Any]


class TargetRedSkyClient:
    """Typed client for official Target web data origins.

    The client deliberately has no proxy rotation, challenge bypass, or browser
    impersonation. A rejected request backs off and fails closed.
    """

    def __init__(self, settings: TargetWatcherSettings):
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(settings.request_concurrency)

    async def __aenter__(self) -> "TargetRedSkyClient":
        timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://www.target.com",
                "Referer": "https://www.target.com/",
            },
            raise_for_status=False,
            auto_decompress=True,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_sitemap(
        self,
        url: str,
        *,
        etag: str = "",
        last_modified: str = "",
        attempts: int = 3,
    ) -> TargetBinaryDocument:
        if not str(url).startswith("https://www.target.com/"):
            raise ValueError("Target sitemap requests must use www.target.com")
        headers = {"Accept": "application/xml,text/xml,application/gzip,*/*;q=0.5"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        response = await self._request(url, headers=headers, attempts=attempts)
        async with response:
            if response.status == 304:
                return TargetBinaryDocument(
                    url=str(response.url),
                    status=304,
                    body=b"",
                    etag=response.headers.get("ETag", etag),
                    last_modified=response.headers.get("Last-Modified", last_modified),
                    not_modified=True,
                )
            if response.status != 200:
                body = " ".join((await response.text(errors="replace")).split())[:300]
                raise RuntimeError(
                    f"Target sitemap returned HTTP {response.status}: {body}"
                )
            limit = max(1, int(self.settings.sitemap_max_compressed_bytes))
            body = await response.content.read(limit + 1)
            if len(body) > limit:
                raise RuntimeError("Target sitemap exceeded the compressed-size safety limit")
            return TargetBinaryDocument(
                url=str(response.url),
                status=response.status,
                body=body,
                etag=response.headers.get("ETag", ""),
                last_modified=response.headers.get("Last-Modified", ""),
            )

    async def search_products(
        self,
        query: str,
        *,
        offset: int = 0,
        count: int = 24,
    ) -> TargetJSONDocument:
        clean_query = " ".join(str(query or "").split())
        if not clean_query:
            raise ValueError("Target search requires a query")
        params = self._geo_params()
        params.update(
            {
                "key": self.settings.redsky_api_key,
                "channel": "WEB",
                "count": str(max(1, min(24, int(count)))),
                "offset": str(max(0, int(offset))),
                "page": f"/s/{clean_query}",
                "platform": "desktop",
                "keyword": clean_query,
                "default_purchasability_filter": "true",
                "include_sponsored": "false",
                "scheduled_delivery_store_id": self.settings.store_id,
                "store_ids": self.settings.store_id,
                "visitor_id": "sniperplug-target-watcher",
            }
        )
        return await self._fetch_json("plp_search_v2", params)

    async def fetch_product(
        self,
        tcin: str,
        *,
        cache_bust: bool = False,
    ) -> TargetJSONDocument:
        clean_tcin = str(tcin or "").strip()
        if not clean_tcin.isdigit():
            raise ValueError("Target PDP request requires a numeric TCIN")
        params = self._geo_params()
        params.update(
            {
                "key": self.settings.redsky_api_key,
                "tcin": clean_tcin,
                "has_pricing_store_id": "true",
                "visitor_id": "sniperplug-target-watcher",
            }
        )
        if cache_bust:
            params["_"] = str(int(time.time() * 1000))
        return await self._fetch_json("pdp_client_v1", params)

    async def fetch_fulfillment(
        self,
        tcins: list[str],
        *,
        cache_bust: bool = False,
    ) -> TargetJSONDocument:
        clean = list(
            dict.fromkeys(
                str(value or "").strip()
                for value in tcins
                if str(value or "").strip().isdigit()
            )
        )
        if not clean:
            raise ValueError("Target fulfillment request requires numeric TCINs")
        if len(clean) > 24:
            raise ValueError("Target fulfillment requests are bounded to 24 TCINs")
        params = self._geo_params()
        params.update(
            {
                "key": self.settings.redsky_api_key,
                "tcins": ",".join(clean),
                "visitor_id": "sniperplug-target-watcher",
            }
        )
        if cache_bust:
            params["_"] = str(int(time.time() * 1000))
        return await self._fetch_json("product_summary_with_fulfillment_v1", params)

    def _geo_params(self) -> dict[str, str]:
        return {
            "store_id": self.settings.store_id,
            "pricing_store_id": self.settings.store_id,
            "zip": self.settings.zip_code,
            "state": self.settings.state,
            "latitude": self.settings.latitude,
            "longitude": self.settings.longitude,
        }

    async def _fetch_json(
        self,
        endpoint: str,
        params: dict[str, str],
        *,
        attempts: int = 3,
    ) -> TargetJSONDocument:
        url = f"{self.settings.redsky_base_url.rstrip('/')}/{endpoint}?{urlencode(params)}"
        response = await self._request(
            url,
            headers={"Accept": "application/json"},
            attempts=attempts,
        )
        async with response:
            text = await response.text(errors="replace")
            if response.status != 200:
                body = " ".join(text.split())[:300]
                raise RuntimeError(
                    f"Target RedSky returned HTTP {response.status} for {endpoint}: {body}"
                )
            content_type = response.headers.get("Content-Type", "").lower()
            if "json" not in content_type and not text.lstrip().startswith("{"):
                raise RuntimeError(
                    f"Target RedSky returned a non-JSON response for {endpoint}"
                )
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"Target RedSky returned malformed JSON for {endpoint}"
                ) from error
            if not isinstance(payload, dict):
                raise RuntimeError(
                    f"Target RedSky returned a non-object payload for {endpoint}"
                )
            return TargetJSONDocument(
                url=str(response.url),
                status=response.status,
                payload=payload,
            )

    async def _request(
        self,
        url: str,
        *,
        headers: dict[str, str] | None,
        attempts: int,
    ) -> aiohttp.ClientResponse:
        session = self._session
        if session is None:
            raise RuntimeError(
                "TargetRedSkyClient must be used as an async context manager"
            )
        last_error: Exception | None = None
        for attempt in range(max(1, int(attempts))):
            try:
                async with self._semaphore:
                    response = await session.get(
                        url,
                        headers=headers,
                        allow_redirects=True,
                    )
                if response.status in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    retry_after = _retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
                    response.release()
                    await asyncio.sleep(max(retry_after, 1.0 * (2**attempt)))
                    continue
                return response
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_error = error
                if attempt + 1 >= attempts:
                    break
                await asyncio.sleep(1.0 * (2**attempt))
        raise RuntimeError(f"Target request failed: {last_error}") from last_error


def _retry_after_seconds(value: Any) -> float:
    try:
        return max(0.0, min(120.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
