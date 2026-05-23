from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.local_inventory import clearance_signal_from_price
from sniperplug.providers.base import DealProvider, ProviderCapability, ProviderHealth, ProviderScanRequest, ProviderScanResult, ProviderStatus


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
            ProviderCapability.CLEARANCE_SIGNAL,
        }
    )

    def __init__(self, config: SerpApiHomeDepotConfig | None = None) -> None:
        self.config = config or serpapi_home_depot_config_from_env()

    async def healthcheck(self) -> ProviderHealth:
        if not self.config.configured:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                status=ProviderStatus.DISABLED,
                message="SerpApi Home Depot search disabled: set SERPAPI_API_KEY.",
            )
        return ProviderHealth(
            provider_key=self.provider_key,
            ok=True,
            status=ProviderStatus.READY,
            message="Ready: SerpApi Home Depot search is configured.",
        )

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        health = await self.healthcheck()
        if not health.ok:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=(health.message,))
        if not request.query:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=("Home Depot SerpApi scan skipped: query required.",))

        try:
            payload = await asyncio.to_thread(self._search, request)
        except SerpApiHomeDepotError as exc:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=(str(exc),))

        candidates = tuple(self._candidates_from_payload(payload, request))
        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=candidates,
            warnings=tuple(_warnings_from_payload(payload)),
            total_results=_int_or_none(payload.get("search_information", {}).get("total_results")),
            page=max(1, request.page),
            page_size=len(candidates),
            has_next_page=bool(payload.get("pagination", {}).get("next")),
            metadata={"query": request.query or "", **request.metadata},
        )

    def _search(self, request: ProviderScanRequest) -> dict:
        params = {
            "engine": "home_depot",
            "q": request.query or "",
            "api_key": self.config.api_key or "",
            "ps": "24",
            "no_cache": "false",
        }
        store_id = request.metadata.get("store_id")
        zip_code = request.metadata.get("zip_code") or request.metadata.get("delivery_zip")
        if store_id:
            params["store_id"] = store_id
        if zip_code:
            params["delivery_zip"] = zip_code
        if request.sort:
            params["sort"] = request.sort
        if request.page > 1:
            params["nao"] = str((request.page - 1) * 24)

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
        product_url = _clean_str(item.get("link") or item.get("product_link"))
        product_id = _clean_str(item.get("product_id") or item.get("productId") or item.get("item_id"))
        if not product_url and product_id:
            product_url = f"https://www.homedepot.com/p/{product_id}"
        if not title or not product_url:
            return None

        price = _price_from_item(item)
        signals = ["SerpApi Home Depot search result; not an in-store scan confirmation"]
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

        return SourceCandidate(
            source_key=self.provider_key,
            retailer="Home Depot",
            title=title,
            product_url=product_url,
            current_price=price,
            typical_price=None,
            image_url=_clean_str(item.get("thumbnail") or item.get("image")),
            product_id=product_id,
            product_id_type="sku" if product_id else None,
            sku=product_id,
            stock_status=availability,
            can_add_to_cart=None,
            signals=signals[:8],
        )


class SerpApiHomeDepotError(RuntimeError):
    pass


def serpapi_home_depot_config_from_env() -> SerpApiHomeDepotConfig:
    return SerpApiHomeDepotConfig(api_key=os.getenv("SERPAPI_API_KEY", "").strip() or None)


def _warnings_from_payload(payload: dict) -> list[str]:
    warnings: list[str] = []
    if payload.get("search_metadata", {}).get("status") and payload.get("search_metadata", {}).get("status") != "Success":
        warnings.append(f"SerpApi status: {payload['search_metadata']['status']}")
    return warnings


def _price_from_item(item: dict[str, Any]) -> float | None:
    for key in ("price", "primary_offer", "price_from", "price_to"):
        parsed = _price_from_value(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _price_from_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("price", "amount", "value", "raw", "extracted_price"):
            parsed = _price_from_value(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _availability_text(item: dict[str, Any]) -> str | None:
    for key in ("availability", "stock", "store_stock", "general_stock"):
        value = item.get(key)
        if isinstance(value, dict):
            text = value.get("status") or value.get("text") or value.get("availability")
            if text:
                return str(text)
        elif value:
            return str(value)
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
