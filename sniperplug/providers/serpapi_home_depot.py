from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.local_inventory import clearance_signal_from_price
from sniperplug.providers.base import DealProvider, ProviderCapability, ProviderHealth, ProviderScanRequest, ProviderScanResult, ProviderStatus


SEARCH_CACHE_MINUTES = 15
_CACHE_DB: Any = None


def configure_home_depot_search_cache(db: Any) -> None:
    global _CACHE_DB
    _CACHE_DB = db


@dataclass(frozen=True)
class SerpApiHomeDepotConfig:
    api_key: str | None = None
    timeout_seconds: int = 15

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


class SerpApiHomeDepotProvider(DealProvider):
    provider_key = "home_depot_serpapi"
    display_name = "Home Depot SerpApi"
    search_url = "https://serpapi.com/search.json"
    capabilities = frozenset(
        {
            ProviderCapability.PRODUCT_LOOKUP,
            ProviderCapability.CATEGORY_SCAN,
            ProviderCapability.OFFER_CHECK,
            ProviderCapability.LOCAL_PRICE,
            ProviderCapability.LOCAL_INVENTORY,
            ProviderCapability.CLEARANCE_SIGNAL,
        }
    )

    def __init__(self, config: SerpApiHomeDepotConfig | None = None) -> None:
        self.config = config or serpapi_home_depot_config_from_env()

    async def healthcheck(self) -> ProviderHealth:
        if not self.config.configured:
            return ProviderHealth(provider_key=self.provider_key, ok=False, status=ProviderStatus.DISABLED, message="SerpApi Home Depot search disabled: set SERPAPI_API_KEY.")
        return ProviderHealth(provider_key=self.provider_key, ok=True, status=ProviderStatus.READY, message="Ready: SerpApi Home Depot search is configured.")

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        health = await self.healthcheck()
        if not health.ok:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=(health.message,))
        if not request.query:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=("Home Depot SerpApi scan skipped: query required.",))

        params = self._search_params(request)
        db = request.metadata.get("db") or _CACHE_DB
        cache_key = _cache_key(params)
        cache_hit = False
        cache_only = bool(request.metadata.get("cache_only"))
        try:
            if db is not None:
                cached = await db.get_provider_cache(self.provider_key, cache_key)
                if cached:
                    payload = cached["response"]
                    cache_hit = True
                elif cache_only:
                    return ProviderScanResult(
                        provider_key=self.provider_key,
                        candidates=(),
                        warnings=("Home Depot search cache miss; live SerpApi call blocked by quota guard.",),
                        metadata={"cache_hit": False, "cache_only": True},
                    )
                else:
                    payload = await asyncio.to_thread(self._fetch_json, params)
                    await db.set_provider_cache(self.provider_key, cache_key, payload, request=_safe_params(params), expires_at=_expires_at(SEARCH_CACHE_MINUTES))
            elif cache_only:
                return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=("Home Depot search cache unavailable; live SerpApi call blocked by quota guard.",), metadata={"cache_hit": False, "cache_only": True})
            else:
                payload = await asyncio.to_thread(self._fetch_json, params)
        except SerpApiHomeDepotError as exc:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=(str(exc),), metadata={"cache_hit": cache_hit})
        except Exception as exc:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=(f"Home Depot cache/search error: {exc}",), metadata={"cache_hit": cache_hit})

        candidates = tuple(self._candidates_from_payload(payload, request))
        warnings = list(_warnings_from_payload(payload))
        if cache_hit:
            warnings.append("Home Depot search cache hit: reused recent SerpApi payload.")
        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=candidates,
            warnings=tuple(warnings),
            total_results=_int_or_none(payload.get("search_information", {}).get("total_results")),
            page=max(1, request.page),
            page_size=len(candidates),
            has_next_page=bool(payload.get("pagination", {}).get("next")),
            metadata={"query": request.query or "", "cache_hit": cache_hit, **{k: v for k, v in request.metadata.items() if k != "db"}},
        )

    def _search_params(self, request: ProviderScanRequest) -> dict[str, str]:
        params = {"engine": "home_depot", "q": request.query or "", "api_key": self.config.api_key or "", "ps": str(max(1, min(request.max_results or 24, 24))), "no_cache": "false"}
        store_id = request.metadata.get("store_id")
        zip_code = request.metadata.get("zip_code") or request.metadata.get("delivery_zip")
        if store_id:
            params["store_id"] = str(store_id)
        if zip_code:
            params["delivery_zip"] = str(zip_code)
        if request.sort:
            params["hd_sort"] = request.sort
        if request.page > 1:
            params["nao"] = str((request.page - 1) * 24)
        return params

    def _search(self, request: ProviderScanRequest) -> dict:
        return self._fetch_json(self._search_params(request))

    def _fetch_json(self, params: dict[str, str]) -> dict:
        url = f"{self.search_url}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            raise SerpApiHomeDepotError(f"SerpApi HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise SerpApiHomeDepotError(f"SerpApi network error: {exc.reason}") from exc

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SerpApiHomeDepotError("SerpApi returned non-JSON response.") from exc
        if not isinstance(decoded, dict):
            raise SerpApiHomeDepotError("SerpApi returned unexpected payload shape.")
        if decoded.get("error"):
            raise SerpApiHomeDepotError(f"SerpApi error: {decoded['error']}")
        return decoded

    def _candidates_from_payload(self, payload: dict, request: ProviderScanRequest) -> list[SourceCandidate]:
        results = payload.get("products") or payload.get("organic_results") or []
        if not isinstance(results, list):
            return []
        candidates: list[SourceCandidate] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            candidate = self._candidate_from_item(item, request)
            if candidate:
                candidates.append(candidate)
        return candidates

    def _candidate_from_item(self, item: dict[str, Any], request: ProviderScanRequest) -> SourceCandidate | None:
        title = _clean_str(item.get("title") or item.get("name"))
        product_id = _clean_str(item.get("product_id") or item.get("productId") or item.get("item_id"))
        product_url, normalized_from_api_host = _home_depot_product_url(item, product_id)
        if not title or not product_url:
            return None

        price = _price_from_item(item)
        typical_price = _typical_price_from_item(item, current_price=price)
        attrs = _variant_attributes_from_item(item)
        if product_id:
            attrs.setdefault("internet_number", product_id)
        signals = ["SerpApi Home Depot search result; not an in-store scan confirmation"]
        if normalized_from_api_host:
            signals.append("product link normalized to Home Depot public URL")
        if price is None:
            signals.append("Home Depot current price not returned by SerpApi")
        if typical_price is None:
            signals.append("Home Depot was/typical price not returned by SerpApi")
        else:
            signals.append("Home Depot was/typical price returned by SerpApi")
        if attrs.get("price_saving"):
            signals.append(f"Home Depot saving: {attrs['price_saving']}")
        if attrs.get("percentage_off"):
            signals.append(f"Home Depot percent off: {attrs['percentage_off']}")
        if attrs.get("price_badge"):
            signals.append(f"Home Depot badge: {attrs['price_badge']}")
        if request.metadata.get("store_id"):
            signals.append(f"store_id: {request.metadata['store_id']}")
        if request.metadata.get("zip_code") or request.metadata.get("delivery_zip"):
            signals.append(f"zip: {request.metadata.get('zip_code') or request.metadata.get('delivery_zip')}")
        clearance_signal = clearance_signal_from_price(price)
        if clearance_signal:
            signals.append(f"clearance price-ending signal: .{clearance_signal.price_ending} ({clearance_signal.stage.value})")

        availability = _availability_text(item)
        if availability:
            signals.append(availability)

        return SourceCandidate(source_key=self.provider_key, retailer="Home Depot", title=title, product_url=product_url, current_price=price, typical_price=typical_price, image_url=_image_url_from_item(item), product_id=product_id, product_id_type="home_depot_product_id" if product_id else None, sku=attrs.get("store_sku_number") or product_id, upc=attrs.get("upc"), model=attrs.get("model_number"), variant_attributes=attrs, stock_status=availability, can_add_to_cart=_bool_or_none(item.get("add_to_cart")), signals=signals[:14])


class SerpApiHomeDepotError(RuntimeError):
    pass


def serpapi_home_depot_config_from_env() -> SerpApiHomeDepotConfig:
    return SerpApiHomeDepotConfig(api_key=os.getenv("SERPAPI_API_KEY", "").strip() or None)


def _warnings_from_payload(payload: dict) -> list[str]:
    warnings: list[str] = []
    if payload.get("search_metadata", {}).get("status") and payload.get("search_metadata", {}).get("status") != "Success":
        warnings.append(f"SerpApi status: {payload['search_metadata']['status']}")
    return warnings


def _home_depot_product_url(item: dict[str, Any], product_id: str | None) -> tuple[str | None, bool]:
    candidates = [item.get("product_page_url"), item.get("product_link"), item.get("link"), item.get("url")]
    for candidate in candidates:
        normalized, from_api_host = _normalize_home_depot_url(_clean_str(candidate))
        if normalized:
            return normalized, from_api_host
    if product_id:
        return f"https://www.homedepot.com/p/{product_id}", False
    return None, False


def _normalize_home_depot_url(raw_url: str | None) -> tuple[str | None, bool]:
    if not raw_url:
        return None, False
    if raw_url.startswith("/p/"):
        return f"https://www.homedepot.com{raw_url}", False
    parsed = urllib.parse.urlparse(raw_url)
    if not parsed.netloc:
        return None, False
    host = parsed.netloc.lower()
    if host in {"apionline.homedepot.com", "www.apionline.homedepot.com"} and parsed.path.startswith("/p/"):
        return urllib.parse.urlunparse(("https", "www.homedepot.com", parsed.path, "", parsed.query, "")), True
    if host.endswith("homedepot.com") and parsed.path.startswith("/p/"):
        return urllib.parse.urlunparse(("https", "www.homedepot.com", parsed.path, "", parsed.query, "")), False
    return raw_url, False


def _price_from_item(item: dict[str, Any]) -> float | None:
    for key in ("price", "primary_offer", "price_from", "price_to", "current_price", "sale_price", "salePrice", "store_price", "storePrice"):
        parsed = _price_from_value(item.get(key))
        if parsed is not None:
            return parsed
    for nested_key in ("pricing", "price_info", "priceInfo", "offer", "offers"):
        parsed = _price_from_value(item.get(nested_key))
        if parsed is not None:
            return parsed
    return None


def _typical_price_from_item(item: dict[str, Any], current_price: float | None) -> float | None:
    keys = ("price_was", "priceWas", "original_price", "originalPrice", "was_price", "wasPrice", "list_price", "listPrice", "regular_price", "regularPrice", "retail_price", "retailPrice", "strikethrough_price", "strikeThroughPrice", "comparison_price", "comparisonPrice", "msrp")
    for key in keys:
        parsed = _price_from_value(item.get(key))
        if _is_valid_typical_price(parsed, current_price):
            return parsed
    for nested_key in ("pricing", "price_info", "priceInfo", "offer", "offers", "primary_offer", "promotion"):
        parsed = _nested_typical_price(item.get(nested_key), current_price)
        if parsed is not None:
            return parsed
    return None


def _nested_typical_price(value: Any, current_price: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            parsed = _nested_typical_price(item, current_price)
            if parsed is not None:
                return parsed
        return None
    if not isinstance(value, dict):
        return None
    for key in ("price_was", "priceWas", "original_price", "originalPrice", "was_price", "wasPrice", "list_price", "listPrice", "regular_price", "regularPrice", "retail_price", "retailPrice", "strikethrough_price", "strikeThroughPrice", "comparison_price", "comparisonPrice", "msrp", "before_price", "beforePrice", "original"):
        parsed = _price_from_value(value.get(key))
        if _is_valid_typical_price(parsed, current_price):
            return parsed
    return None


def _is_valid_typical_price(value: float | None, current_price: float | None) -> bool:
    if value is None or value <= 0:
        return False
    if current_price is None:
        return True
    return value > current_price


def _variant_attributes_from_item(item: dict[str, Any]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    mapping = {"product_id": "internet_number", "productId": "internet_number", "store_sku_number": "store_sku_number", "storeSkuNumber": "store_sku_number", "upc": "upc", "brand": "brand", "model_number": "model_number", "modelNumber": "model_number", "unit": "unit", "price_badge": "price_badge", "priceBadge": "price_badge", "percentage_off": "percentage_off", "percent_off": "percentage_off", "percentOff": "percentage_off", "favorite": "favorite_count", "favorites": "favorite_count", "rating": "rating", "reviews": "reviews", "price_saving": "price_saving", "priceSaving": "price_saving", "collection": "collection"}
    for raw_key, attr_key in mapping.items():
        value = _clean_str(item.get(raw_key))
        if value:
            attrs[attr_key] = value
    badges = item.get("badges")
    if isinstance(badges, list):
        cleaned_badges = [str(badge).strip() for badge in badges if str(badge).strip()]
        if cleaned_badges:
            attrs["badges"] = ", ".join(cleaned_badges[:5])
    delivery_text = _fulfillment_text(item.get("delivery"), prefix="Delivery")
    if delivery_text:
        attrs["delivery"] = delivery_text
    pickup_text = _fulfillment_text(item.get("pickup"), prefix="Pickup")
    if pickup_text:
        attrs["pickup"] = pickup_text
    fulfillment = item.get("fulfillment")
    if isinstance(fulfillment, dict):
        store = _clean_str(fulfillment.get("store"))
        if store:
            attrs["fulfillment_store"] = store
        quantity = _clean_str(fulfillment.get("quantity") or fulfillment.get("countity"))
        if quantity:
            attrs["fulfillment_quantity"] = quantity
    stock_information = item.get("stock_information")
    if isinstance(stock_information, dict):
        for raw_key, attr_key in (("general_stock", "general_stock"), ("general_stock_status", "general_stock_status"), ("store_stock", "store_stock"), ("store_stock_status", "store_stock_status")):
            value = _clean_str(stock_information.get(raw_key))
            if value:
                attrs[attr_key] = value
    for raw_key, attr_key in (("add_to_cart", "add_to_cart"), ("buy_online_pay_in_store", "buy_online_pay_in_store"), ("check_nearby_stores", "check_nearby_stores")):
        value = _bool_or_none(item.get(raw_key))
        if value is not None:
            attrs[attr_key] = "yes" if value else "no"
    return attrs


def _fulfillment_text(value: Any, prefix: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        positive: list[str] = []
        negative: list[str] = []
        for key, raw in value.items():
            parsed = _bool_or_none(raw)
            label = key.replace("_", " ")
            if parsed is True:
                positive.append(label)
            elif parsed is False and key in {"out_of_stock", "not_available_for_delivery"}:
                negative.append(f"not {label}")
        if positive:
            return f"{prefix}: " + ", ".join(positive[:4])
        if negative:
            return f"{prefix}: " + ", ".join(negative[:2])
    return None


def _image_url_from_item(item: dict[str, Any]) -> str | None:
    direct = _clean_str(item.get("thumbnail") or item.get("image"))
    if direct:
        return direct
    images = item.get("images")
    if isinstance(images, list):
        for entry in images:
            if isinstance(entry, dict):
                candidate = _clean_str(entry.get("link") or entry.get("url") or entry.get("image"))
                if candidate:
                    return candidate
            else:
                candidate = _clean_str(entry)
                if candidate:
                    return candidate
    thumbnails = item.get("thumbnails")
    if isinstance(thumbnails, list):
        for entry in thumbnails:
            if isinstance(entry, list):
                for nested in entry:
                    candidate = _clean_str(nested)
                    if candidate:
                        return candidate
            else:
                candidate = _clean_str(entry)
                if candidate:
                    return candidate
    return None


def _price_from_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("price", "amount", "value", "raw", "extracted_price", "current", "current_price", "sale", "sale_price"):
            parsed = _price_from_value(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, list):
        for item in value:
            parsed = _price_from_value(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\$?\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)", value)
        if not match:
            return None
        cleaned = match.group(1).replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _availability_text(item: dict[str, Any]) -> str | None:
    stock_information = item.get("stock_information")
    if isinstance(stock_information, dict):
        store_status = _clean_str(stock_information.get("store_stock_status"))
        store_qty = _clean_str(stock_information.get("store_stock"))
        general_status = _clean_str(stock_information.get("general_stock_status"))
        if store_status and store_qty:
            return f"Store stock: {store_qty} ({store_status})"
        if store_status:
            return f"Store stock: {store_status}"
        if general_status:
            return f"General stock: {general_status}"
    for key in ("availability", "stock", "store_stock", "general_stock"):
        value = item.get(key)
        if isinstance(value, dict):
            text = value.get("status") or value.get("text") or value.get("availability")
            if text:
                return str(text)
        elif value:
            return str(value)
    return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "available", "seen"}:
            return True
        if text in {"false", "no", "0", "unavailable", "not seen"}:
            return False
    return None


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cache_key(params: dict[str, str]) -> str:
    safe = _safe_params(params)
    return urllib.parse.urlencode(sorted(safe.items()))


def _safe_params(params: dict[str, str]) -> dict[str, str]:
    return {str(k): str(v) for k, v in params.items() if k != "api_key"}


def _expires_at(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
