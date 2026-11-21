"""Home Depot direct lane: gateway-primary provider with SerpApi fallback.

Registered under the existing ``home_depot_serpapi`` provider key so
every current call site (HD search, standing hunts, local scan) keeps
working unchanged. Each scan tries the direct federation-gateway lane
first (0 SerpApi credits); any direct failure — block, breaker open,
timeout, malformed or empty — falls back to SerpApi for that call only.

Result metadata always carries ``lane`` ("direct" or
"serpapi_fallback"), ``serpapi_credit`` (1 only when a live SerpApi call
was actually spent; 0 for direct success or a SerpApi cache hit) and, on
fallback, ``fallback_reason``. Callers charge the quota guard from
``serpapi_credit`` so a direct success is never billed as a SerpApi call.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.local_inventory import clearance_signal_from_price
from sniperplug.providers import serpapi_home_depot as _serpapi_module
from sniperplug.providers.base import (
    DealProvider,
    ProviderCapability,
    ProviderHealth,
    ProviderScanRequest,
    ProviderScanResult,
    ProviderStatus,
)
from sniperplug.providers.serpapi_home_depot import SerpApiHomeDepotProvider
from sniperplug.services import hd_direct_client
from sniperplug.services.hd_direct_identity import direct_lane_enabled

logger = logging.getLogger(__name__)

SEARCH_CACHE_MINUTES = 15
_CACHE_DB: Any = None


def configure_home_depot_direct_cache(db: Any) -> None:
    global _CACHE_DB
    _CACHE_DB = db


def _expires_at(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _cache_key(request: ProviderScanRequest) -> str:
    params = {
        "q": (request.query or "").strip().lower(),
        "store_id": str(request.metadata.get("store_id") or ""),
        "zip": str(request.metadata.get("zip_code") or request.metadata.get("delivery_zip") or ""),
        "page": str(max(1, request.page)),
        "ps": str(max(1, min(request.max_results or 24, 24))),
    }
    return urllib.parse.urlencode(sorted(params.items()))


def _image_url(product: dict[str, Any]) -> str | None:
    media = product.get("media") if isinstance(product.get("media"), dict) else {}
    images = media.get("images") if isinstance(media, dict) else None
    if isinstance(images, list):
        for image in images:
            if isinstance(image, dict) and image.get("url"):
                return str(image["url"]).replace("<SIZE>", "400")
    return None


def _anchor_inventory(product: dict[str, Any]) -> tuple[int | None, str | None]:
    fulfillment = product.get("fulfillment") if isinstance(product.get("fulfillment"), dict) else {}
    options = fulfillment.get("fulfillmentOptions") if isinstance(fulfillment, dict) else None
    quantity: int | None = None
    for option in options or []:
        if not isinstance(option, dict):
            continue
        for service in option.get("services") or []:
            if not isinstance(service, dict):
                continue
            for location in service.get("locations") or []:
                if not isinstance(location, dict) or not location.get("isAnchor"):
                    continue
                inventory = location.get("inventory") if isinstance(location.get("inventory"), dict) else {}
                raw_qty = inventory.get("quantity")
                if isinstance(raw_qty, (int, float)):
                    quantity = int(raw_qty)
                if inventory.get("isInStock"):
                    return quantity, "in_stock"
                if inventory.get("isOutOfStock"):
                    return quantity, "out_of_stock"
    anchor_status = _clean_str(fulfillment.get("anchorStoreStatusType"))
    return quantity, anchor_status


def parse_search_product(product: dict[str, Any], request: ProviderScanRequest) -> SourceCandidate | None:
    """Parse one gateway searchModel product into a SourceCandidate."""
    if not isinstance(product, dict):
        return None
    identifiers = product.get("identifiers") if isinstance(product.get("identifiers"), dict) else {}
    title = _clean_str(identifiers.get("productLabel"))
    item_id = _clean_str(product.get("itemId"))
    if not title or not item_id:
        return None
    pricing = product.get("pricing") if isinstance(product.get("pricing"), dict) else {}
    price = _money(pricing.get("value"))
    original = _money(pricing.get("original"))
    if original is not None and (price is None or original <= price):
        original = None

    canonical = _clean_str(identifiers.get("canonicalUrl"))
    if canonical and canonical.startswith("/"):
        canonical = f"https://www.homedepot.com{canonical}"
    product_url = canonical if canonical and canonical.startswith("http") else f"https://www.homedepot.com/p/{item_id}"

    attrs: dict[str, str] = {"internet_number": item_id}
    for raw_key, attr_key in (
        ("storeSkuNumber", "store_sku_number"),
        ("brandName", "brand"),
        ("modelNumber", "model_number"),
    ):
        value = _clean_str(identifiers.get(raw_key))
        if value:
            attrs[attr_key] = value
    quantity, stock_status = _anchor_inventory(product)
    if quantity is not None:
        attrs["fulfillment_quantity"] = str(quantity)
    if request.metadata.get("store_id"):
        attrs["fulfillment_store"] = str(request.metadata["store_id"])

    signals = ["Home Depot direct gateway result; not an in-store scan confirmation"]
    badges = product.get("badges") if isinstance(product.get("badges"), list) else []
    badge_names = [str(b.get("name")) for b in badges if isinstance(b, dict) and b.get("name")]
    if badge_names:
        signals.append("Badges: " + ", ".join(badge_names[:4]))
    if price is None:
        signals.append("Home Depot current price not returned by direct gateway")
    if original is None:
        signals.append("Home Depot was/typical price not returned by direct gateway")
    else:
        signals.append("Home Depot was/typical price returned by direct gateway")
    if quantity is not None:
        signals.append(f"Anchor store quantity: {quantity}")
    if request.metadata.get("store_id"):
        signals.append(f"store_id: {request.metadata['store_id']}")
    zip_value = request.metadata.get("zip_code") or request.metadata.get("delivery_zip")
    if zip_value:
        signals.append(f"zip: {zip_value}")
    clearance_signal = clearance_signal_from_price(price)
    if clearance_signal:
        signals.append(f"clearance price-ending signal: .{clearance_signal.price_ending} ({clearance_signal.stage.value})")
    availability = product.get("availabilityType") if isinstance(product.get("availabilityType"), dict) else {}
    if availability.get("discontinued"):
        signals.append("Availability type: discontinued")

    return SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title=title,
        product_url=product_url,
        current_price=price,
        typical_price=original,
        image_url=_image_url(product),
        product_id=item_id,
        product_id_type="home_depot_product_id",
        sku=_clean_str(identifiers.get("storeSkuNumber")) or item_id,
        model=_clean_str(identifiers.get("modelNumber")),
        variant_attributes=attrs,
        stock_status=stock_status,
        signals=signals[:14],
    )


class DirectHomeDepotProvider(DealProvider):
    """Search via Home Depot's own federation gateway (no SerpApi)."""

    provider_key = "home_depot_direct"
    display_name = "Home Depot (direct gateway)"
    capabilities = frozenset(
        {
            ProviderCapability.PRODUCT_LOOKUP,
            ProviderCapability.CATEGORY_SCAN,
            ProviderCapability.LOCAL_PRICE,
            ProviderCapability.LOCAL_INVENTORY,
            ProviderCapability.CLEARANCE_SIGNAL,
        }
    )

    async def healthcheck(self) -> ProviderHealth:
        status = hd_direct_client.lane_status()
        if not status["enabled"]:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                status=ProviderStatus.DISABLED,
                message="Home Depot direct lane disabled (SNIPERPLUG_HD_DIRECT_ENABLED=false).",
            )
        try:
            import curl_cffi  # noqa: F401
        except Exception as exc:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                status=ProviderStatus.ERROR,
                message=f"Home Depot direct lane unavailable: curl_cffi failed to import ({exc}).",
            )
        if status["breaker_open"]:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                status=ProviderStatus.ERROR,
                message=(
                    "Home Depot direct lane circuit breaker is open (identity-suspect: repeated "
                    "gateway refusals). SerpApi fallback carries traffic until the cooldown ends."
                ),
            )
        return ProviderHealth(
            provider_key=self.provider_key,
            ok=True,
            status=ProviderStatus.READY,
            message=f"Ready: Home Depot direct gateway lane (Chrome {status['identity_major']} identity).",
        )

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        query = (request.query or "").strip()
        if not query:
            return ProviderScanResult(
                provider_key=self.provider_key,
                candidates=(),
                warnings=("Home Depot direct scan skipped: query required.",),
            )
        db = request.metadata.get("db") or _CACHE_DB or _serpapi_module._CACHE_DB
        cache_key = _cache_key(request)
        if db is not None:
            try:
                cached = await db.get_provider_cache(self.provider_key, cache_key)
            except Exception:
                cached = None
            if cached:
                payload = cached.get("response") if isinstance(cached, dict) else None
                result = self._result_from_payload(payload, request, cache_hit=True)
                if result is not None:
                    return result
        zip_code = _clean_str(request.metadata.get("zip_code") or request.metadata.get("delivery_zip"))
        store_id = _clean_str(request.metadata.get("store_id"))
        payload = await hd_direct_client.search_model(
            query,
            zip_code=zip_code,
            store_id=store_id,
            page=max(1, int(request.page or 1)),
            page_size=max(1, min(24, int(request.max_results or 24))),
        )
        result = self._result_from_payload(payload, request, cache_hit=False)
        if result is None:
            raise hd_direct_client.HdDirectError("Home Depot gateway returned an unparseable search payload")
        if db is not None and result.candidates:
            try:
                await db.set_provider_cache(
                    self.provider_key,
                    cache_key,
                    payload,
                    request={"q": query, "store_id": store_id or "", "zip": zip_code or ""},
                    expires_at=_expires_at(SEARCH_CACHE_MINUTES),
                )
            except Exception:
                logger.debug("home depot direct cache write failed", exc_info=True)
        return result

    def _result_from_payload(
        self, payload: Any, request: ProviderScanRequest, *, cache_hit: bool
    ) -> ProviderScanResult | None:
        if not isinstance(payload, dict):
            return None
        model = (payload.get("data") or {}).get("searchModel")
        if not isinstance(model, dict):
            return None
        products = model.get("products")
        if not isinstance(products, list):
            return None
        candidates = tuple(
            c for c in (parse_search_product(p, request) for p in products if isinstance(p, dict)) if c is not None
        )
        metadata_block = model.get("metadata") if isinstance(model.get("metadata"), dict) else {}
        total = metadata_block.get("totalProducts")
        if not isinstance(total, int):
            report = model.get("searchReport") if isinstance(model.get("searchReport"), dict) else {}
            total = report.get("totalProducts")
        page_size = max(1, min(24, int(request.max_results or 24)))
        start = (max(1, int(request.page or 1)) - 1) * page_size
        warnings: tuple[str, ...] = ()
        if cache_hit:
            warnings = ("Home Depot direct search cache hit: reused recent gateway payload.",)
        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=candidates,
            warnings=warnings,
            total_results=total if isinstance(total, int) else None,
            page=max(1, int(request.page or 1)),
            page_size=len(candidates),
            has_next_page=isinstance(total, int) and total > start + len(products),
            metadata={
                "query": request.query or "",
                "cache_hit": cache_hit,
                "lane": "direct",
                "serpapi_credit": 0,
                **{k: v for k, v in request.metadata.items() if k != "db"},
            },
        )


class HomeDepotLaneProvider(DealProvider):
    """Composite registered as ``home_depot_serpapi``: direct, then SerpApi."""

    provider_key = "home_depot_serpapi"
    display_name = "Home Depot"
    capabilities = SerpApiHomeDepotProvider.capabilities

    def __init__(
        self,
        direct: DirectHomeDepotProvider | None = None,
        serpapi: SerpApiHomeDepotProvider | None = None,
    ) -> None:
        self.direct = direct or DirectHomeDepotProvider()
        self.serpapi = serpapi or SerpApiHomeDepotProvider()

    async def healthcheck(self) -> ProviderHealth:
        direct_health = await self.direct.healthcheck()
        serpapi_health = await self.serpapi.healthcheck()
        if direct_health.ok:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=True,
                status=ProviderStatus.READY,
                message=f"{direct_health.message} SerpApi fallback configured: {serpapi_health.ok}.",
            )
        if serpapi_health.ok:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=True,
                status=ProviderStatus.READY,
                message=f"Ready: SerpApi Home Depot search is configured. Direct lane: {direct_health.message}",
            )
        return ProviderHealth(
            provider_key=self.provider_key,
            ok=False,
            status=serpapi_health.status,
            message=f"{serpapi_health.message} Direct lane: {direct_health.message}",
        )

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        fallback_reason: str | None = None
        if direct_lane_enabled():
            try:
                direct_result = await self.direct.scan(request)
                if direct_result.candidates or direct_result.total_results is not None:
                    return ProviderScanResult(
                        provider_key=self.provider_key,
                        candidates=direct_result.candidates,
                        warnings=direct_result.warnings,
                        total_results=direct_result.total_results,
                        page=direct_result.page,
                        page_size=direct_result.page_size,
                        has_next_page=direct_result.has_next_page,
                        metadata={**direct_result.metadata, "lane": "direct", "serpapi_credit": 0},
                    )
                fallback_reason = "direct lane returned an empty/unparseable page"
            except Exception as exc:
                fallback_reason = f"{type(exc).__name__}: {exc}"
                logger.info("HD direct lane unavailable, falling back to SerpApi: %s", fallback_reason)
        else:
            fallback_reason = "direct lane disabled"

        serpapi_request = request
        if request.metadata.get("quota_blocked") or request.metadata.get("cache_only"):
            # The caller's quota guard refused a SerpApi spend: the
            # fallback may only serve the SerpApi cache, never a live call.
            serpapi_request = ProviderScanRequest(
                source_key=request.source_key,
                category=request.category,
                query=request.query,
                product_ids=request.product_ids,
                max_results=request.max_results,
                page=request.page,
                sort=request.sort,
                order=request.order,
                metadata={**request.metadata, "cache_only": True},
            )
        serpapi_result = await self.serpapi.scan(serpapi_request)
        credit = 0 if (serpapi_result.metadata.get("cache_hit") or serpapi_request is not request) else 1
        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=serpapi_result.candidates,
            warnings=serpapi_result.warnings,
            total_results=serpapi_result.total_results,
            page=serpapi_result.page,
            page_size=serpapi_result.page_size,
            has_next_page=serpapi_result.has_next_page,
            metadata={
                **serpapi_result.metadata,
                "lane": "serpapi_fallback",
                "serpapi_credit": credit,
                "fallback_reason": fallback_reason,
            },
        )


def direct_identity_summary() -> dict[str, Any]:
    """Health-surface summary: identity profiles + breaker state."""
    from sniperplug.services.hd_direct_identity import identity_for, sweep_majors

    return {
        "profiles": [identity_for(m).impersonate for m in sweep_majors()],
        **hd_direct_client.lane_status(),
    }


__all__ = [
    "DirectHomeDepotProvider",
    "HomeDepotLaneProvider",
    "configure_home_depot_direct_cache",
    "direct_identity_summary",
    "parse_search_product",
]
