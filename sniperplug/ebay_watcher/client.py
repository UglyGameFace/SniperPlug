from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import logging
import time
from typing import Any, Mapping
from urllib.parse import quote

import aiohttp

from sniperplug.ebay_watcher.config import EbayWatcherSettings
from sniperplug.ebay_watcher.models import EbayWatchRule


log = logging.getLogger("sniperplug.ebay_watcher.http")
BROWSE_SCOPE = "https://api.ebay.com/oauth/api_scope"


@dataclass(frozen=True)
class EbayJSONResponse:
    url: str
    status: int
    payload: dict[str, Any]
    request_id: str = ""


class EbayBrowseClient:
    def __init__(self, settings: EbayWatcherSettings):
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(settings.request_concurrency)
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self.calls_made = 0

    async def __aenter__(self) -> "EbayBrowseClient":
        timeout = aiohttp.ClientTimeout(total=self.settings.request_timeout_seconds)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "application/json",
                "Accept-Language": "en-US",
            },
            raise_for_status=False,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def search(self, rule: EbayWatchRule) -> EbayJSONResponse:
        if not rule.has_search_identity:
            raise ValueError("eBay watch rule has no search identity")
        params: dict[str, str] = {
            "limit": str(max(1, min(200, int(rule.search_limit)))),
            "fieldgroups": "EXTENDED",
            "filter": self._search_filter(rule),
            "sort": "newlyListed",
        }
        if rule.query:
            params["q"] = rule.query
        if rule.category_id:
            params["category_ids"] = rule.category_id
        if rule.gtin:
            params["gtin"] = rule.gtin
        if rule.epid:
            params["epid"] = rule.epid
        return await self._authorized_json(
            "GET",
            f"{self.settings.api_base_url}/buy/browse/v1/item_summary/search",
            params=params,
        )

    async def get_item(self, item_id: str) -> EbayJSONResponse:
        clean_id = str(item_id or "").strip()
        if not clean_id:
            raise ValueError("eBay item ID is required")
        return await self._authorized_json(
            "GET",
            f"{self.settings.api_base_url}/buy/browse/v1/item/{quote(clean_id, safe='')}",
        )

    async def get_items(self, item_ids: list[str]) -> EbayJSONResponse:
        clean_ids = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in item_ids
                if str(value).strip()
            )
        )
        if not clean_ids:
            raise ValueError("At least one eBay item ID is required")
        if len(clean_ids) > 20:
            raise ValueError("The eBay watcher intentionally limits getItems batches to 20")
        return await self._authorized_json(
            "GET",
            f"{self.settings.api_base_url}/buy/browse/v1/item/",
            params={"item_ids": ",".join(clean_ids)},
        )

    async def _access_token_value(self, *, force_refresh: bool = False) -> str:
        now = time.monotonic()
        if (
            not force_refresh
            and self._access_token
            and now < self._access_token_expires_at
        ):
            return self._access_token

        session = self._require_session()
        credentials = base64.b64encode(
            f"{self.settings.client_id}:{self.settings.client_secret}".encode("utf-8")
        ).decode("ascii")
        response = await self._request(
            "POST",
            f"{self.settings.identity_base_url}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": BROWSE_SCOPE,
            },
            authorized=False,
            attempts=3,
        )
        async with response:
            payload = await _json_payload(response)
            if response.status != 200:
                raise RuntimeError(
                    "eBay OAuth token request failed "
                    f"HTTP {response.status}: {_error_text(payload)}"
                )
        token = str(payload.get("access_token") or "").strip()
        expires_in = _positive_int(payload.get("expires_in")) or 7200
        if not token:
            raise RuntimeError("eBay OAuth response did not include an access_token")
        self._access_token = token
        self._access_token_expires_at = now + max(60, expires_in - 60)
        return token

    async def _authorized_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> EbayJSONResponse:
        token = await self._access_token_value()
        response = await self._request(
            method,
            url,
            params=params,
            headers=self._browse_headers(token),
            authorized=True,
            attempts=3,
        )
        async with response:
            payload = await _json_payload(response)
            if response.status == 401:
                token = await self._access_token_value(force_refresh=True)
            else:
                return self._finish_json(response, payload)

        response = await self._request(
            method,
            url,
            params=params,
            headers=self._browse_headers(token),
            authorized=True,
            attempts=2,
        )
        async with response:
            payload = await _json_payload(response)
            return self._finish_json(response, payload)

    def _finish_json(
        self,
        response: aiohttp.ClientResponse,
        payload: dict[str, Any],
    ) -> EbayJSONResponse:
        if response.status != 200:
            raise RuntimeError(
                f"eBay Browse request returned HTTP {response.status}: "
                f"{_error_text(payload)}"
            )
        return EbayJSONResponse(
            url=str(response.url),
            status=response.status,
            payload=payload,
            request_id=(
                response.headers.get("X-EBAY-C-REQUEST-ID")
                or response.headers.get("X-EBAY-REQUEST-ID")
                or ""
            ),
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        authorized: bool,
        attempts: int,
    ) -> aiohttp.ClientResponse:
        session = self._require_session()
        last_error: Exception | None = None
        for attempt in range(max(1, int(attempts))):
            try:
                async with self._semaphore:
                    response = await session.request(
                        method,
                        url,
                        params=params,
                        headers=dict(headers or {}),
                        data=data,
                        allow_redirects=True,
                    )
                if authorized:
                    self.calls_made += 1
                if (
                    response.status in {429, 500, 502, 503, 504}
                    and attempt + 1 < attempts
                ):
                    retry_after = _retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
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
        raise RuntimeError(f"eBay request failed for {url}: {last_error}") from last_error

    def _browse_headers(self, token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": self.settings.marketplace_id,
        }
        if self.settings.buyer_postal_code:
            location = (
                f"country={self.settings.buyer_country},"
                f"zip={self.settings.buyer_postal_code}"
            )
            headers["X-EBAY-C-ENDUSERCTX"] = (
                f"contextualLocation={quote(location, safe='')}"
            )
        return headers

    def _search_filter(self, rule: EbayWatchRule) -> str:
        filters = [
            "buyingOptions:{FIXED_PRICE}",
            f"deliveryCountry:{self.settings.buyer_country}",
        ]
        if rule.seller:
            filters.append(f"sellers:{{{rule.seller}}}")
        return ",".join(filters)

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError(
                "EbayBrowseClient must be used as an async context manager"
            )
        return self._session


async def _json_payload(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        payload = await response.json(content_type=None)
    except Exception:
        text = await response.text(errors="replace")
        return {"raw": text[:1000]}
    return payload if isinstance(payload, dict) else {"data": payload}


def _error_text(payload: Mapping[str, Any]) -> str:
    errors = payload.get("errors")
    if isinstance(errors, list):
        parts = []
        for error in errors[:5]:
            if isinstance(error, Mapping):
                parts.append(
                    " ".join(
                        str(
                            error.get("message")
                            or error.get("longMessage")
                            or error.get("errorId")
                            or "unknown eBay error"
                        ).split()
                    )
                )
        if parts:
            return " | ".join(parts)[:1000]
    return " ".join(str(payload.get("raw") or payload).split())[:1000]


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _retry_after_seconds(value: Any) -> float:
    try:
        return max(0.0, min(120.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
