"""Direct Home Depot federation-gateway client (curl_cffi lane).

Measured recipe (Nov 2026 research + live POC): cookieless POSTs to the
gateway with one consistent Chrome identity (see hd_direct_identity).
Plain requests/httpx always return HTTP 206, so this module is the only
place the direct lane talks HTTP, and it goes through curl_cffi only.

Failure discipline: HTTP 206/403/429 mean the (IP, identity) pair is
being refused. The client tries a short identity sweep once per call,
then stops — a circuit breaker suspends the lane after a few
consecutively blocked calls so the bot never hammers a burned route
while SerpApi carries traffic.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from sniperplug.services.hd_direct_identity import (
    GATEWAY_HOSTS,
    OP_HEADER_ATTEMPTS,
    direct_lane_enabled,
    identity_for,
    sweep_majors,
)

logger = logging.getLogger(__name__)

BLOCKED_STATUSES = frozenset({206, 401, 403, 407, 429})
_REQUEST_TIMEOUT_SECONDS = 20.0


class HdDirectError(Exception):
    """Direct lane failed for a non-block reason (fallback is safe)."""


class HdDirectBlockedError(HdDirectError):
    """The gateway refused this (IP, identity) pair or the breaker is open."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int((os.getenv(name) or "").strip() or default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float((os.getenv(name) or "").strip() or default))
    except ValueError:
        return default


@dataclass
class DirectBreaker:
    """Consecutive-block circuit breaker for the direct lane."""

    threshold: int = field(default_factory=lambda: _env_int("SNIPERPLUG_HD_DIRECT_BREAKER_THRESHOLD", 3))
    cooldown_seconds: float = field(
        default_factory=lambda: _env_float("SNIPERPLUG_HD_DIRECT_COOLDOWN_HOURS", 6.0) * 3600.0
    )
    consecutive_blocks: int = 0
    open_until: float = 0.0
    working_major: int | None = None
    last_status: int | None = None
    last_success_at: float | None = None
    last_block_at: float | None = None

    def is_open(self, now: float | None = None) -> bool:
        return self.open_until > (time.time() if now is None else now)

    def record_success(self, major: int) -> None:
        self.consecutive_blocks = 0
        self.open_until = 0.0
        self.working_major = int(major)
        self.last_success_at = time.time()

    def record_blocked(self, status: int | None) -> bool:
        """Count one fully-blocked call; True when the breaker opens."""
        self.consecutive_blocks += 1
        self.last_status = status
        self.last_block_at = time.time()
        if self.consecutive_blocks >= self.threshold:
            self.open_until = self.last_block_at + self.cooldown_seconds
            logger.warning(
                "HD direct lane identity-suspect: %s consecutive blocked calls "
                "(last HTTP %s); lane suspended for %.1f hours, SerpApi carries traffic.",
                self.consecutive_blocks,
                status,
                self.cooldown_seconds / 3600.0,
            )
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        return {
            "breaker_open": self.is_open(),
            "consecutive_blocks": self.consecutive_blocks,
            "breaker_threshold": self.threshold,
            "cooldown_hours": self.cooldown_seconds / 3600.0,
            "open_until": self.open_until or None,
            "working_major": self.working_major,
            "last_status": self.last_status,
            "last_success_at": self.last_success_at,
            "last_block_at": self.last_block_at,
        }


_BREAKER = DirectBreaker()


def breaker() -> DirectBreaker:
    return _BREAKER


def lane_status() -> dict[str, Any]:
    from sniperplug.services.hd_direct_identity import default_chrome_major

    snap = _BREAKER.snapshot()
    return {
        "enabled": direct_lane_enabled(),
        "identity_major": snap["working_major"] or default_chrome_major(),
        **snap,
    }


# ---------------------------------------------------------------------------
# GraphQL documents. The search document was rebuilt 2026-11-22 against
# the live gateway after Home Depot revised the schema (the Nov 2026
# document's search arguments and several fields no longer exist, and
# the gateway now rejects unused variable declarations). Both documents
# carry only what the providers actually parse; the rebuilt shapes were
# verified live through the lane before shipping.
# ---------------------------------------------------------------------------

SEARCH_QUERY = """
query searchModel(
  $storeId: String,
  $keyword: String,
  $isBrandPricingPolicyCompliant: Boolean
) {
  searchModel(
    keyword: $keyword
  ) {
    keyword
    searchReport { totalProducts }
    products {
      itemId
      identifiers { canonicalUrl productLabel brandName modelNumber storeSkuNumber }
      pricing(storeId: $storeId, isBrandPricingPolicyCompliant: $isBrandPricingPolicyCompliant) {
        value original
      }
      media { images { url type subType sizes } }
      badges { name }
      availabilityType { type discontinued }
      fulfillment(storeId: $storeId) {
        anchorStoreStatusType
        fulfillmentOptions {
          fulfillable type
          services { type locations { locationId isAnchor inventory { quantity isInStock isOutOfStock } } }
        }
      }
    }
  }
}
"""

PRODUCT_QUERY = """
query productClientOnlyProduct(
  $itemId: String!,
  $dataSource: String,
  $loyaltyMembershipInput: LoyaltyMembershipInput,
  $storeId: String,
  $isBrandPricingPolicyCompliant: Boolean,
  $skipSpecificationGroup: Boolean = false,
  $zipCode: String,
  $quantity: Int,
) {
  product(itemId: $itemId, dataSource: $dataSource, loyaltyMembershipInput: $loyaltyMembershipInput) {
    itemId
    identifiers { canonicalUrl brandName productLabel storeSkuNumber upc upcGtin13 }
    pricing(storeId: $storeId, isBrandPricingPolicyCompliant: $isBrandPricingPolicyCompliant) {
      value original unitOfMeasure
      clearance { value dollarOff percentageOff unitsClearancePrice }
    }
    fulfillment(storeId: $storeId, zipCode: $zipCode, quantity: $quantity) {
      anchorStoreStatusType onlineStoreStatusType
      fulfillmentOptions {
        fulfillable type
        services { type locations { locationId inventory { isOutOfStock quantity isInStock isLimitedQuantity isUnavailable } } }
      }
    }
    media { images { url type subType sizes } }
    specificationGroup @skip(if: $skipSpecificationGroup) { specTitle specifications { specName specValue } }
  }
}
"""


# ---------------------------------------------------------------------------
# Transport (single seam; tests replace _execute_attempt)
# ---------------------------------------------------------------------------


_PROXY_ENV = "SNIPERPLUG_HD_DIRECT_PROXY_URLS"
_proxy_counter = itertools.count()


def _proxy_urls() -> tuple[str, ...]:
    """Configured egress proxies for the direct lane (comma-separated).

    The gateway refuses most datacenter addresses (HTTP 206); routing
    through residential addresses is what makes the lane answer. Blank
    or unset means the container's own address, as before.
    """
    raw = (os.getenv(_PROXY_ENV) or "").strip()
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _proxy_for_attempt(attempt_index: int) -> str | None:
    urls = _proxy_urls()
    if not urls:
        return None
    return urls[(next(_proxy_counter) + attempt_index) % len(urls)]


def _execute_attempt(
    major: int,
    attempt_index: int,
    opname: str,
    body: dict[str, Any],
    referer: str,
) -> tuple[int, dict[str, Any] | None]:
    """One real gateway POST. Returns (status, parsed_json_or_None).

    A fresh curl_cffi session per identity: no cookies are set, stored,
    or sent — the session is discarded with the attempt.
    """
    from curl_cffi import requests as cffi_requests

    identity = identity_for(major)
    headers = identity.headers(opname, attempt_index, referer=referer)
    last_error: Exception | None = None
    for host in GATEWAY_HOSTS:
        url = f"https://{host}/federation-gateway/graphql?opname={opname}"
        try:
            session_kwargs: dict[str, Any] = {"impersonate": identity.impersonate}
            proxy_url = _proxy_for_attempt(attempt_index)
            if proxy_url:
                session_kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
            with cffi_requests.Session(**session_kwargs) as session:
                response = session.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
            try:
                return int(response.status_code), json.loads(response.text or "")
            except Exception:
                return int(response.status_code), None
        except Exception as exc:  # connection-level: try the mirror host once
            last_error = exc
            continue
    raise HdDirectError(f"Home Depot gateway unreachable: {last_error}")


def _payload_has_data(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    return any(value is not None for value in data.values())


def _is_generic_error_payload(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    return any(isinstance(key, str) and "generic" in key.lower() for key in data.keys())


def graphql_sync(opname: str, query: str, variables: dict[str, Any], *, referer: str) -> dict[str, Any]:
    """Run one gateway operation with the identity sweep + breaker.

    Raises HdDirectBlockedError when every identity is refused (and
    counts the call toward the breaker), HdDirectError for request or
    transport failures that are not blocks.
    """
    if not direct_lane_enabled():
        raise HdDirectError("direct lane disabled (SNIPERPLUG_HD_DIRECT_ENABLED=false)")
    if _BREAKER.is_open():
        raise HdDirectBlockedError(
            "direct lane circuit breaker open; SerpApi fallback carries traffic",
            status=_BREAKER.last_status,
        )
    body = {"operationName": opname, "variables": variables, "query": query}
    attempts = OP_HEADER_ATTEMPTS.get(opname) or (("general-merchandise", "general-merchandise"),)
    majors = sweep_majors()
    working = _BREAKER.working_major
    if working is not None and working in majors:
        majors = (working, *(m for m in majors if m != working))
    last_status: int | None = None
    for profile_index, major in enumerate(majors):
        # Header variants are only worth spending on the first profile;
        # later profiles get the primary routing headers only.
        header_attempts = range(len(attempts)) if profile_index == 0 else range(1)
        for attempt_index in header_attempts:
            status, payload = _execute_attempt(major, attempt_index, opname, body, referer)
            if status in BLOCKED_STATUSES or _is_generic_error_payload(payload):
                last_status = status
                break  # this identity is refused; move to the next profile
            if status != 200:
                raise HdDirectError(f"Home Depot gateway HTTP {status} for {opname}")
            if not _payload_has_data(payload):
                errors = payload.get("errors") if isinstance(payload, dict) else None
                raise HdDirectError(f"Home Depot gateway returned no data for {opname}: {errors}")
            _BREAKER.record_success(major)
            return payload
    _BREAKER.record_blocked(last_status)
    raise HdDirectBlockedError(
        f"Home Depot gateway refused every identity for {opname} (last HTTP {last_status})",
        status=last_status,
    )


async def graphql(opname: str, query: str, variables: dict[str, Any], *, referer: str) -> dict[str, Any]:
    return await asyncio.to_thread(graphql_sync, opname, query, variables, referer=referer)


async def search_model(
    keyword: str,
    *,
    zip_code: str | None,
    store_id: str | None,
    page: int = 1,
    page_size: int = 24,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(24, int(page_size or 24)))
    # The gateway's current schema takes no pagination, channel or
    # delivery-ZIP search arguments: it returns its default first page
    # (24 products). page/page_size stay in the signature for callers;
    # store context still flows through the pricing/fulfillment fields.
    variables: dict[str, Any] = {
        "keyword": keyword,
        "isBrandPricingPolicyCompliant": False,
    }
    if store_id:
        variables["storeId"] = str(store_id)
    referer = f"https://www.homedepot.com/s/{keyword.replace(' ', '%20')}"
    return await graphql("searchModel", SEARCH_QUERY, variables, referer=referer)


async def product_detail(
    item_id: str,
    *,
    store_id: str | None,
    zip_code: str | None,
) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "itemId": str(item_id),
        "isBrandPricingPolicyCompliant": True,
        "skipSpecificationGroup": True,
    }
    if store_id:
        variables["storeId"] = str(store_id)
    if zip_code:
        variables["zipCode"] = str(zip_code)
    referer = f"https://www.homedepot.com/p/{item_id}"
    return await graphql("productClientOnlyProduct", PRODUCT_QUERY, variables, referer=referer)
