from __future__ import annotations

"""Official eBay Browse research. Active offers are NOT completed sales.

The entire module uses the official fixed origin, client-credentials OAuth and
request budget; neither a user-supplied URL nor API-returned itemHref is fetched.
Do not use prices from this module as proof of resale profit.
"""

import asyncio
import base64
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
import re
import time
from typing import Any
from urllib.parse import urlparse, quote

import aiohttp

OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
QUERY_MAX = 100
PAGE_SIZE = 15
REQUEST_TIMEOUT_SECONDS = 12
MAX_RESPONSE_BYTES = 500_000
BROWSE_REQUESTS_PER_MINUTE = 8


class EbayApiError(Exception):
    """Safe, credential-free message for the Discord caller."""


@dataclass(frozen=True)
class EbaySettings:
    client_id: str = ""
    client_secret: str = ""
    sandbox: bool = False
    campaign_id: str = ""
    messaging_approved: bool = False
    research_enabled: bool = False

    @classmethod
    def from_env(cls) -> "EbaySettings":
        def enabled(key: str) -> bool:
            return os.getenv(key, "").strip().lower() in {"true", "1", "yes", "on"}
        return cls(
            client_id=os.getenv("EBAY_CLIENT_ID", "").strip(),
            client_secret=os.getenv("EBAY_CLIENT_SECRET", "").strip(),
            sandbox=enabled("EBAY_SANDBOX"),
            campaign_id=os.getenv("EBAY_EPN_CAMPAIGN_ID", "").strip(),
            messaging_approved=enabled("EBAY_EPN_MESSAGING_APPROVED"),
            research_enabled=enabled("EBAY_RESEARCH_ENABLED"),
        )

    @property
    def api_base(self) -> str:
        return "https://api.sandbox.ebay.com" if self.sandbox else "https://api.ebay.com"

    @property
    def credentials_ready(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def affiliate_ready(self) -> bool:
        return bool(
            not self.sandbox
            and self.research_enabled
            and self.credentials_ready
            and self.messaging_approved
            and re.fullmatch(r"[0-9]{10}", self.campaign_id)
        )


@dataclass(frozen=True)
class EbayListing:
    item_id: str
    title: str
    price_cents: int
    currency: str
    condition: str
    seller: str
    shipping_cents: int | None
    asking_url: str
    affiliate_url: str
    image_url: str = ""

    @property
    def delivered_cents(self) -> int | None:
        if self.shipping_cents is None:
            return None
        return self.price_cents + self.shipping_cents


def _cents(value: Any) -> int | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not amount.is_finite() or amount < 0 or amount > 10_000_000:
        return None
    cents = amount * 100
    return int(cents) if cents == cents.to_integral_value() else None


def _ebay_item_url(value: Any) -> str:
    """Only allow real public item links; never return API or arbitrary domains."""
    url = str(value or "").strip()
    if len(url) > 1900:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "ebay.com" or host.endswith(".ebay.com")):
        return ""
    try:
        invalid_port = parsed.port not in (None, 443)
    except ValueError:
        return ""
    if not parsed.path.startswith("/itm/") or parsed.username or parsed.password or invalid_port:
        return ""
    return url


def parse_listing(payload: Any) -> EbayListing | None:
    if not isinstance(payload, dict):
        return None
    options = payload.get("buyingOptions")
    if not isinstance(options, list) or "FIXED_PRICE" not in options:
        return None
    price = payload.get("price")
    if not isinstance(price, dict) or price.get("currency") != "USD":
        return None
    price_cents = _cents(price.get("value"))
    if price_cents is None or price_cents < 1:
        return None
    item_id = str(payload.get("itemId") or "").strip()
    title = " ".join(str(payload.get("title") or "").split())[:180]
    url = _ebay_item_url(payload.get("itemWebUrl"))
    if not item_id or len(item_id) > 150 or not title or not url:
        return None
    shipping = None
    shipping_options = payload.get("shippingOptions")
    if isinstance(shipping_options, list):
        for option in shipping_options:
            if not isinstance(option, dict):
                continue
            cost = option.get("shippingCost")
            if isinstance(cost, dict) and cost.get("currency") == "USD":
                shipping = _cents(cost.get("value"))
                if shipping is not None:
                    break
    seller = payload.get("seller")
    seller_id = str(seller.get("username") or "")[:80] if isinstance(seller, dict) else ""
    image = payload.get("image")
    image_url = str(image.get("imageUrl") or "")[:800] if isinstance(image, dict) else ""
    return EbayListing(
        item_id=item_id,
        title=title,
        price_cents=price_cents,
        currency="USD",
        condition=str(payload.get("condition") or "Unspecified")[:70],
        seller=seller_id,
        shipping_cents=shipping,
        asking_url=url,
        affiliate_url=_ebay_item_url(payload.get("itemAffiliateWebUrl")),
        image_url=image_url,
    )


class EbayBrowse:
    """Small credential-scoped client, no shared Discord gateway blocking.

    One in-flight call and an 8/min local budget. Separate processes cannot
    bypass approval because outbound affiliate messages have their own gate.
    """

    def __init__(self, settings: EbaySettings):
        self.settings = settings
        self._token = ""
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()
        self._calls: list[float] = []

    async def search(self, *, query: str = "", gtin: str = "", limit: int = PAGE_SIZE) -> tuple[EbayListing, ...]:
        if not self.settings.research_enabled:
            raise EbayApiError("eBay research is disabled in the bot environment.")
        if not self.settings.credentials_ready:
            raise EbayApiError("Production eBay Developer Client ID and Secret are not configured.")
        query = " ".join(str(query or "").split())
        gtin = str(gtin or "").strip()
        if bool(query) == bool(gtin):
            raise EbayApiError("Supply exactly one search term or GTIN.")
        if query and (len(query) > QUERY_MAX or any(ord(c) < 32 for c in query)):
            raise EbayApiError("Search must contain 1 to 100 printable characters.")
        if gtin and (not gtin.isdigit() or len(gtin) not in {8, 12, 13, 14}):
            raise EbayApiError("Use an 8-, 12-, 13-, or 14-digit GTIN.")
        count = min(PAGE_SIZE, max(1, int(limit)))
        async with self._lock:
            now = time.monotonic()
            self._calls = [t for t in self._calls if now - t < 60]
            if len(self._calls) >= BROWSE_REQUESTS_PER_MINUTE:
                raise EbayApiError("eBay research request budget reached. Retry after one minute.")
            self._calls.append(now)
            token = await self._get_token()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)) as session:
                params = {
                    "limit": str(count),
                    "filter": "buyingOptions:{FIXED_PRICE},deliveryCountry:US",
                }
                if query:
                    params["q"] = query
                else:
                    params["gtin"] = gtin
                headers = {
                    "Authorization": f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
                    "Accept": "application/json",
                    "Accept-Language": "en-US",
                }
                if re.fullmatch(r"[0-9]{10}", self.settings.campaign_id):
                    headers["X-EBAY-C-ENDUSERCTX"] = f"affiliateCampaignId={self.settings.campaign_id}"
                payload, status = await self._json(
                    session, "GET",
                    self.settings.api_base + "/buy/browse/v1/item_summary/search",
                    headers=headers, params=params,
                )
                if status == 401:
                    self._token = ""
                    token = await self._get_token(session=session)
                    headers["Authorization"] = f"Bearer {token}"
                    payload, status = await self._json(
                        session, "GET",
                        self.settings.api_base + "/buy/browse/v1/item_summary/search",
                        headers=headers, params=params,
                    )
            if status != 200:
                raise EbayApiError(f"eBay Browse API unavailable (HTTP {status}); check key access, permissions and quota.")
        values = payload.get("itemSummaries") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            return ()
        listings = [parse_listing(value) for value in values[:count]]
        return tuple(value for value in listings if value is not None)

    async def _get_token(self, *, session: aiohttp.ClientSession | None = None) -> str:
        if self._token and time.monotonic() + 60 < self._token_expiry:
            return self._token
        credentials = base64.b64encode(
            f"{self.settings.client_id}:{self.settings.client_secret}".encode()
        ).decode("ascii")
        if session is None:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)) as owned:
                data, status = await self._json(
                    owned, "POST",
                    self.settings.api_base + "/identity/v1/oauth2/token",
                    headers={"Authorization": f"Basic {credentials}"},
                    form={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
                )
        else:
            data, status = await self._json(
                session, "POST",
                self.settings.api_base + "/identity/v1/oauth2/token",
                headers={"Authorization": f"Basic {credentials}"},
                form={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
            )
        if status != 200 or not isinstance(data, dict) or not str(data.get("access_token") or ""):
            raise EbayApiError(f"eBay OAuth failed (HTTP {status}); verify your Developer keyset and permissions.")
        self._token = str(data["access_token"])
        try:
            ttl = min(7200, max(120, int(data.get("expires_in") or 7200)))
        except (ValueError, TypeError):
            ttl = 3600
        self._token_expiry = time.monotonic() + ttl
        return self._token

    @staticmethod
    async def _json(session, method: str, url: str, *, headers: dict[str, str], params=None, form=None):
        try:
            req = session.request(
                method, url, headers=headers, params=params, data=form,
                allow_redirects=False,
            )
            async with req as response:
                status = int(response.status)
                if status in {301, 302, 303, 307, 308}:
                    raise EbayApiError("eBay returned a redirect; request blocked.")
                raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise EbayApiError("eBay response exceeded the bounded size limit.")
                if status == 429:
                    raise EbayApiError("eBay rate-limited the request; try again later.")
                if status >= 500:
                    raise EbayApiError(f"eBay API temporarily unavailable (HTTP {status}).")
                if not raw:
                    return {}, status
                import json
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise EbayApiError(f"eBay returned invalid data (HTTP {status}).") from None
                return data if isinstance(data, dict) else {}, status
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise EbayApiError("eBay request failed or timed out; no listing evidence was collected.") from None
