from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


SERPAPI_URL = "https://serpapi.com/search.json"
REFERENCE_PRICE_KEYS = (
    "original",
    "original_price",
    "originalPrice",
    "price_was",
    "priceWas",
    "was_price",
    "wasPrice",
    "list_price",
    "listPrice",
    "regular_price",
    "regularPrice",
    "retail_price",
    "retailPrice",
    "strikethrough_price",
    "strikeThroughPrice",
    "comparison_price",
    "comparisonPrice",
    "msrp",
    "before_price",
    "beforePrice",
)
PRODUCT_DETAIL_CACHE_MINUTES = 20
_CACHE_DB: Any = None


@dataclass(frozen=True)
class HomeDepotFulfillmentOption:
    type: str
    title: str | None = None
    date: str | None = None
    bottom: str | None = None
    quantity: int | None = None

    def label(self) -> str:
        bits = []
        if self.quantity is not None:
            bits.append(f"qty {self.quantity}")
        if self.title:
            bits.append(self.title)
        if self.date:
            bits.append(self.date)
        if self.bottom:
            bits.append(self.bottom)
        return f"{self.type}: " + "; ".join(bits) if bits else self.type


@dataclass(frozen=True)
class HomeDepotProductDetail:
    product_id: str
    title: str | None = None
    link: str | None = None
    image_url: str | None = None
    price: float | None = None
    original_price: float | None = None
    reference_price_source: str | None = None
    upc: str | None = None
    model_number: str | None = None
    store_sku_number: str | None = None
    brand: str | None = None
    rating: str | None = None
    reviews: str | None = None
    fulfillment_store: str | None = None
    fulfillment_quantity: int | None = None
    fulfillment_options: tuple[HomeDepotFulfillmentOption, ...] = ()
    warnings: tuple[str, ...] = ()
    raw_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pickup_quantity(self) -> int | None:
        for option in self.fulfillment_options:
            if "pickup" in option.type.lower() and option.quantity is not None:
                return option.quantity
        return None

    @property
    def ship_quantity(self) -> int | None:
        for option in self.fulfillment_options:
            lowered = option.type.lower()
            if ("ship" in lowered or "delivery" in lowered) and option.quantity is not None:
                return option.quantity
        return None


def configure_home_depot_product_detail_cache(db: Any) -> None:
    global _CACHE_DB
    _CACHE_DB = db


async def fetch_home_depot_product_detail(product_id: str, *, zip_code: str | None = None, store_id: str | None = None, db: Any = None) -> HomeDepotProductDetail | None:
    db = db or _CACHE_DB
    params = _product_params(product_id, zip_code=zip_code, store_id=store_id)
    cache_key = _cache_key(params)
    if db is not None:
        try:
            cached = await db.get_provider_cache("home_depot_product_serpapi", cache_key)
            if cached:
                detail = _detail_from_payload(product_id, cached["response"])
                if detail:
                    return _with_warning(detail, "Product detail cache hit: reused recent Home Depot Product API payload.")
        except Exception:
            pass

    direct_detail = await _fetch_direct_detail(product_id, zip_code=zip_code, store_id=store_id, db=db, cache_key=cache_key)
    if direct_detail is not None:
        return direct_detail

    payload = await asyncio.to_thread(_fetch_product_payload_sync, params)
    if db is not None and isinstance(payload, dict) and not payload.get("_sniperplug_error") and not payload.get("error"):
        try:
            await db.set_provider_cache(
                "home_depot_product_serpapi",
                cache_key,
                payload,
                request=_safe_params(params),
                expires_at=_expires_at(PRODUCT_DETAIL_CACHE_MINUTES),
            )
        except Exception:
            pass
    return _detail_from_payload(product_id, payload)


DIRECT_DETAIL_WARNING = "Served by the Home Depot direct gateway lane (no SerpApi credit used)."


def _detail_from_direct_payload(product_id: str, payload: Any, *, store_id: str | None = None, zip_code: str | None = None) -> HomeDepotProductDetail | None:
    """Parse a productClientOnlyProduct gateway payload into a detail.

    Returns None when the payload carries no usable price, so the caller
    falls back to SerpApi instead of trusting a hollow record.
    """
    if not isinstance(payload, dict):
        return None
    product = ((payload.get("data") or {}).get("product") or {})
    if not isinstance(product, dict) or not product:
        return None
    identifiers = product.get("identifiers") if isinstance(product.get("identifiers"), dict) else {}
    pricing = product.get("pricing") if isinstance(product.get("pricing"), dict) else {}
    price = _number(pricing.get("value"))
    if price is None:
        return None
    original = _number(pricing.get("original"))
    if original is not None and original <= price:
        original = None

    image_url: str | None = None
    media = product.get("media") if isinstance(product.get("media"), dict) else {}
    images = media.get("images") if isinstance(media, dict) else None
    if isinstance(images, list):
        for image in images:
            if isinstance(image, dict) and image.get("url"):
                image_url = str(image["url"]).replace("<SIZE>", "400")
                break

    fulfillment = product.get("fulfillment") if isinstance(product.get("fulfillment"), dict) else {}
    options: list[HomeDepotFulfillmentOption] = []
    fulfillment_quantity: int | None = None
    raw_options = fulfillment.get("fulfillmentOptions") if isinstance(fulfillment, dict) else None
    for option in raw_options or []:
        if not isinstance(option, dict):
            continue
        quantities: list[int] = []
        for service in option.get("services") or []:
            if not isinstance(service, dict):
                continue
            for location in service.get("locations") or []:
                if not isinstance(location, dict):
                    continue
                if store_id and str(location.get("locationId") or "") not in {"", str(store_id)}:
                    continue
                inventory = location.get("inventory") if isinstance(location.get("inventory"), dict) else {}
                qty = _int(inventory.get("quantity"))
                if qty is not None:
                    quantities.append(qty)
        option_quantity = sum(quantities) if quantities else None
        option_type = _clean(option.get("type")) or "Fulfillment"
        options.append(HomeDepotFulfillmentOption(type=option_type, quantity=option_quantity))
        if option_quantity is not None:
            if "pickup" in option_type.lower() or fulfillment_quantity is None:
                fulfillment_quantity = option_quantity

    canonical = _clean(identifiers.get("canonicalUrl"))
    if canonical and canonical.startswith("/"):
        canonical = f"https://www.homedepot.com{canonical}"
    return HomeDepotProductDetail(
        product_id=_clean(product.get("itemId")) or product_id,
        title=_clean(identifiers.get("productLabel")),
        link=canonical or f"https://www.homedepot.com/p/{product_id}",
        image_url=image_url,
        price=price,
        original_price=original,
        reference_price_source="direct pricing.original" if original is not None else None,
        upc=_clean(identifiers.get("upc")) or _clean(identifiers.get("upcGtin13")),
        store_sku_number=_clean(identifiers.get("storeSkuNumber")),
        brand=_clean(identifiers.get("brandName")),
        fulfillment_store=_clean(store_id),
        fulfillment_quantity=fulfillment_quantity,
        fulfillment_options=tuple(options),
        warnings=(DIRECT_DETAIL_WARNING,),
        raw_keys=tuple(product.keys()),
    )


async def _fetch_direct_detail(product_id: str, *, zip_code: str | None = None, store_id: str | None = None, db: Any = None, cache_key: str) -> HomeDepotProductDetail | None:
    """Try the direct gateway lane for a product detail; None = fall back."""
    from sniperplug.services import hd_direct_client

    if db is not None:
        try:
            cached = await db.get_provider_cache("home_depot_product_direct", cache_key)
            if cached:
                detail = _detail_from_direct_payload(product_id, cached["response"], store_id=store_id, zip_code=zip_code)
                if detail is not None:
                    return _with_warning(detail, "Product detail cache hit: reused recent direct gateway payload.")
        except Exception:
            pass
    try:
        payload = await hd_direct_client.product_detail(product_id, store_id=store_id, zip_code=zip_code)
    except Exception:
        return None
    detail = _detail_from_direct_payload(product_id, payload, store_id=store_id, zip_code=zip_code)
    if detail is None:
        return None
    if db is not None:
        try:
            await db.set_provider_cache(
                "home_depot_product_direct",
                cache_key,
                payload,
                request={"product_id": product_id, "store_id": store_id or "", "zip": zip_code or ""},
                expires_at=_expires_at(PRODUCT_DETAIL_CACHE_MINUTES),
            )
        except Exception:
            pass
    return detail


def _product_params(product_id: str, *, zip_code: str | None = None, store_id: str | None = None) -> dict[str, str]:
    key = os.getenv("SERPAPI_API_KEY", "").strip()
    params = {"engine": "home_depot_product", "product_id": product_id, "api_key": key, "no_cache": "false"}
    if zip_code:
        params["delivery_zip"] = zip_code
    if store_id:
        params["store_id"] = store_id
    return params


def _fetch_home_depot_product_detail_sync(product_id: str, *, zip_code: str | None = None, store_id: str | None = None) -> HomeDepotProductDetail | None:
    return _detail_from_payload(product_id, _fetch_product_payload_sync(_product_params(product_id, zip_code=zip_code, store_id=store_id)))


def _fetch_product_payload_sync(params: dict[str, str]) -> dict[str, Any]:
    product_id = params.get("product_id") or ""
    if not params.get("api_key"):
        return {"_sniperplug_error": "SERPAPI_API_KEY is not configured for Home Depot Product API detail lookup.", "product_id": product_id}
    try:
        with urllib.request.urlopen(f"{SERPAPI_URL}?{urllib.parse.urlencode(params)}", timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        return {"_sniperplug_error": f"Home Depot Product API HTTP {exc.code}: {body}", "product_id": product_id}
    except Exception as exc:
        return {"_sniperplug_error": f"Home Depot Product API lookup failed: {exc}", "product_id": product_id}
    return payload if isinstance(payload, dict) else {"_sniperplug_error": "Home Depot Product API returned an unexpected payload.", "product_id": product_id}


def _detail_from_payload(product_id: str, payload: dict[str, Any]) -> HomeDepotProductDetail | None:
    if not isinstance(payload, dict):
        return HomeDepotProductDetail(product_id=product_id, warnings=("Home Depot Product API returned an unexpected payload.",))
    if payload.get("_sniperplug_error"):
        return HomeDepotProductDetail(product_id=product_id, warnings=(str(payload["_sniperplug_error"]),))
    if payload.get("error"):
        return HomeDepotProductDetail(product_id=product_id, warnings=(f"Home Depot Product API error: {payload['error']}",))
    item = payload.get("product_results")
    if not isinstance(item, dict):
        return HomeDepotProductDetail(product_id=product_id, warnings=("Home Depot Product API returned no product_results block.",), raw_keys=tuple(payload.keys()))
    return _detail_from_product_results(product_id, item, payload)


def _detail_from_product_results(product_id: str, item: dict[str, Any], payload: dict[str, Any]) -> HomeDepotProductDetail:
    brand = item.get("brand")
    brand_name = _clean(brand.get("name")) if isinstance(brand, dict) else _clean(brand)
    fulfillment = item.get("fulfillment") if isinstance(item.get("fulfillment"), dict) else {}
    options = _fulfillment_options(fulfillment.get("options"))
    warnings = []
    status = payload.get("search_metadata", {}).get("status")
    if status and status != "Success":
        warnings.append(f"SerpApi status: {status}")

    current_price = _number(item.get("price"))
    reference_price, reference_source = _reference_price_from_item(item, current_price)
    if current_price is not None and reference_price is None:
        warnings.append("Home Depot Product API did not return a trustworthy was/MSRP/reference price for this item.")

    return HomeDepotProductDetail(
        product_id=_clean(item.get("product_id")) or product_id,
        title=_clean(item.get("title")),
        link=_clean(item.get("link")) or f"https://www.homedepot.com/p/{product_id}",
        image_url=_first_image(item),
        price=current_price,
        original_price=reference_price,
        reference_price_source=reference_source,
        upc=_clean(item.get("upc")),
        model_number=_clean(item.get("model_number")),
        store_sku_number=_clean(item.get("store_sku_number")),
        brand=brand_name,
        rating=_clean(item.get("rating")),
        reviews=_clean(item.get("reviews")),
        fulfillment_store=_clean(fulfillment.get("store")),
        fulfillment_quantity=_int(fulfillment.get("quantity") or fulfillment.get("countity")),
        fulfillment_options=options,
        warnings=tuple(warnings),
        raw_keys=tuple(item.keys()),
    )


def _reference_price_from_item(item: dict[str, Any], current_price: float | None) -> tuple[float | None, str | None]:
    for key in REFERENCE_PRICE_KEYS:
        parsed = _number(item.get(key))
        if _valid_reference_price(parsed, current_price):
            return parsed, key
    for key in ("promotion", "pricing", "price_info", "priceInfo", "offer", "offers", "primary_offer"):
        parsed, source = _nested_reference_price(item.get(key), current_price, prefix=key)
        if parsed is not None:
            return parsed, source
    return None, None


def _nested_reference_price(value: Any, current_price: float | None, *, prefix: str) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, list):
        for index, item in enumerate(value):
            parsed, source = _nested_reference_price(item, current_price, prefix=f"{prefix}[{index}]")
            if parsed is not None:
                return parsed, source
        return None, None
    if not isinstance(value, dict):
        return None, None
    for key in REFERENCE_PRICE_KEYS:
        parsed = _number(value.get(key))
        if _valid_reference_price(parsed, current_price):
            return parsed, f"{prefix}.{key}"
    for key, nested in value.items():
        if isinstance(nested, (dict, list)):
            parsed, source = _nested_reference_price(nested, current_price, prefix=f"{prefix}.{key}")
            if parsed is not None:
                return parsed, source
    return None, None


def _valid_reference_price(value: float | None, current_price: float | None) -> bool:
    if value is None or value <= 0:
        return False
    if current_price is None:
        return True
    return value > current_price


def _fulfillment_options(raw: Any) -> tuple[HomeDepotFulfillmentOption, ...]:
    if not isinstance(raw, list):
        return ()
    parsed = []
    for option in raw:
        if not isinstance(option, dict):
            continue
        arrival = option.get("arrival_time")
        if isinstance(arrival, list):
            date = ", ".join(str(v).strip() for v in arrival if str(v).strip())
        else:
            date = _clean(arrival) or _clean(option.get("delivery_date"))
        parsed.append(HomeDepotFulfillmentOption(type=_clean(option.get("type")) or "Fulfillment", title=_clean(option.get("title")), date=date, bottom=_clean(option.get("bottom")), quantity=_int(option.get("quantity"))))
    return tuple(parsed)


def _first_image(item: dict[str, Any]) -> str | None:
    for key in ("thumbnail", "image"):
        value = _clean(item.get(key))
        if value:
            return value
    images = item.get("images")
    if isinstance(images, list):
        for image in images:
            value = _clean(image.get("link") or image.get("url") or image.get("image")) if isinstance(image, dict) else _clean(image)
            if value:
                return value
    return None


def _with_warning(detail: HomeDepotProductDetail, warning: str) -> HomeDepotProductDetail:
    return HomeDepotProductDetail(
        product_id=detail.product_id,
        title=detail.title,
        link=detail.link,
        image_url=detail.image_url,
        price=detail.price,
        original_price=detail.original_price,
        reference_price_source=detail.reference_price_source,
        upc=detail.upc,
        model_number=detail.model_number,
        store_sku_number=detail.store_sku_number,
        brand=detail.brand,
        rating=detail.rating,
        reviews=detail.reviews,
        fulfillment_store=detail.fulfillment_store,
        fulfillment_quantity=detail.fulfillment_quantity,
        fulfillment_options=detail.fulfillment_options,
        warnings=detail.warnings + (warning,),
        raw_keys=detail.raw_keys,
    )


def _cache_key(params: dict[str, str]) -> str:
    safe = _safe_params(params)
    return json.dumps(safe, sort_keys=True, separators=(",", ":"))


def _safe_params(params: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in params.items() if key != "api_key"}


def _expires_at(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)", str(value))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None
