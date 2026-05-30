from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


SERPAPI_URL = "https://serpapi.com/search.json"


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


async def fetch_home_depot_product_detail(product_id: str, *, zip_code: str | None = None, store_id: str | None = None) -> HomeDepotProductDetail | None:
    return await asyncio.to_thread(_fetch_home_depot_product_detail_sync, product_id, zip_code=zip_code, store_id=store_id)


def _fetch_home_depot_product_detail_sync(product_id: str, *, zip_code: str | None = None, store_id: str | None = None) -> HomeDepotProductDetail | None:
    key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not key:
        return HomeDepotProductDetail(product_id=product_id, warnings=("SERPAPI_API_KEY is not configured for Home Depot Product API detail lookup.",))

    params = {
        "engine": "home_depot_product",
        "product_id": product_id,
        "api_key": key,
        "no_cache": "false",
    }
    if zip_code:
        params["delivery_zip"] = zip_code
    if store_id:
        params["store_id"] = store_id

    try:
        with urllib.request.urlopen(f"{SERPAPI_URL}?{urllib.parse.urlencode(params)}", timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        return HomeDepotProductDetail(product_id=product_id, warnings=(f"Home Depot Product API HTTP {exc.code}: {body}",))
    except Exception as exc:
        return HomeDepotProductDetail(product_id=product_id, warnings=(f"Home Depot Product API lookup failed: {exc}",))

    if not isinstance(payload, dict):
        return HomeDepotProductDetail(product_id=product_id, warnings=("Home Depot Product API returned an unexpected payload.",))
    if payload.get("error"):
        return HomeDepotProductDetail(product_id=product_id, warnings=(f"Home Depot Product API error: {payload['error']}",))

    item = payload.get("product_results")
    if not isinstance(item, dict):
        return HomeDepotProductDetail(product_id=product_id, warnings=("Home Depot Product API returned no product_results block.",), raw_keys=tuple(payload.keys()))

    return _detail_from_product_results(product_id, item, payload)


def _detail_from_product_results(product_id: str, item: dict[str, Any], payload: dict[str, Any]) -> HomeDepotProductDetail:
    promotion = item.get("promotion") if isinstance(item.get("promotion"), dict) else {}
    brand = item.get("brand")
    brand_name = _clean(brand.get("name")) if isinstance(brand, dict) else _clean(brand)
    fulfillment = item.get("fulfillment") if isinstance(item.get("fulfillment"), dict) else {}
    options = _fulfillment_options(fulfillment.get("options"))
    warnings = []
    status = payload.get("search_metadata", {}).get("status")
    if status and status != "Success":
        warnings.append(f"SerpApi status: {status}")

    return HomeDepotProductDetail(
        product_id=_clean(item.get("product_id")) or product_id,
        title=_clean(item.get("title")),
        link=_clean(item.get("link")) or f"https://www.homedepot.com/p/{product_id}",
        image_url=_first_image(item),
        price=_number(item.get("price")),
        original_price=_number(promotion.get("original")),
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
        parsed.append(
            HomeDepotFulfillmentOption(
                type=_clean(option.get("type")) or "Fulfillment",
                title=_clean(option.get("title")),
                date=date,
                bottom=_clean(option.get("bottom")),
                quantity=_int(option.get("quantity")),
            )
        )
    return tuple(parsed)


def _first_image(item: dict[str, Any]) -> str | None:
    for key in ("thumbnail", "image"):
        value = _clean(item.get(key))
        if value:
            return value
    images = item.get("images")
    if isinstance(images, list):
        for image in images:
            if isinstance(image, dict):
                value = _clean(image.get("link") or image.get("url") or image.get("image"))
            else:
                value = _clean(image)
            if value:
                return value
    return None


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
