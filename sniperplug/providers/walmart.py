from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import DealProvider, ProviderCapability, ProviderHealth, ProviderScanRequest, ProviderScanResult, ProviderStatus
from sniperplug.services.variant_proof import extract_variant_proof


@dataclass(frozen=True)
class WalmartAffiliateConfig:
    consumer_id: str | None = None
    key_version: str = "1"
    private_key_b64: str | None = None
    publisher_id: str | None = None
    enabled: bool = False
    timeout_seconds: int = 12

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.consumer_id and self.private_key_b64)


class WalmartProvider(DealProvider):
    provider_key = "walmart"
    display_name = "Walmart"
    search_url = "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/search"
    taxonomy_url = "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/taxonomy"
    allowed_sorts = {"relevance", "price", "title", "bestseller", "customerRating", "new"}
    capabilities = frozenset({ProviderCapability.PRODUCT_LOOKUP, ProviderCapability.CATEGORY_SCAN, ProviderCapability.IMAGE_LOOKUP, ProviderCapability.OFFER_CHECK, ProviderCapability.MEMBER_PRICING})

    def __init__(self, config: WalmartAffiliateConfig | None = None, configured: bool | None = None):
        if config is None:
            config = walmart_config_from_env(fallback_enabled=bool(configured))
        self.config = config

    async def healthcheck(self) -> ProviderHealth:
        if not self.config.enabled:
            return ProviderHealth(provider_key=self.provider_key, ok=False, status=ProviderStatus.DISABLED, message="Disabled: set WALMART_PROVIDER_ENABLED=true after credentials are configured.")
        missing = self._missing_config()
        if missing:
            return ProviderHealth(provider_key=self.provider_key, ok=False, status=ProviderStatus.ERROR, message=f"Missing Walmart config: {', '.join(missing)}.")
        suffix = " Affiliate tracking enabled." if self.config.publisher_id else " Direct Walmart links only until Impact Publisher ID is added."
        return ProviderHealth(provider_key=self.provider_key, ok=True, status=ProviderStatus.READY, message="Ready: Walmart Affiliate API credentials are configured." + suffix)

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        health = await self.healthcheck()
        if not health.ok:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=(health.message,))
        if not request.query and not request.product_ids:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=("Walmart scan skipped: query or product_ids required.",), page=request.page, page_size=request.max_results)

        warnings: list[str] = []
        if not self.config.publisher_id:
            warnings.append("WALMART_PUBLISHER_ID is blank; using direct Walmart links for personal deal hunting.")

        candidates: list[SourceCandidate] = []
        total_results: int | None = None
        start_index: int | None = None
        page_size = max(1, min(request.max_results, 25))
        queries = [request.query] if request.query else []
        queries.extend(request.product_ids)
        for query in queries:
            if not query:
                continue
            try:
                payload = await asyncio.to_thread(self._search, query=query, request=request, page_size=page_size)
            except WalmartProviderError as exc:
                warnings.append(str(exc))
                continue
            candidates.extend(self._candidates_from_payload(payload, request=request))
            total_results = _int_or_none(payload.get("totalResults")) or total_results
            start_index = _int_or_none(payload.get("start")) or start_index

        if total_results is not None and start_index is not None:
            has_next_page = start_index + page_size <= min(total_results, 1000)
        else:
            has_next_page = len(candidates) >= page_size
        return ProviderScanResult(provider_key=self.provider_key, candidates=tuple(candidates), warnings=tuple(warnings), total_results=total_results, page=max(1, request.page), page_size=page_size, start_index=start_index, has_next_page=has_next_page, metadata={"query": request.query or "", "sort": request.sort or "relevance"})

    def _search(self, query: str, request: ProviderScanRequest, page_size: int) -> dict:
        page = max(1, request.page)
        start = ((page - 1) * page_size) + 1
        params = {"query": query, "numItems": str(page_size), "start": str(start), "responseGroup": "full"}
        if request.sort:
            sort = request.sort.strip()
            if sort in self.allowed_sorts:
                params["sort"] = sort
                if sort == "price" and request.order in {"ascending", "descending"}:
                    params["order"] = request.order
        if self.config.publisher_id:
            params["publisherId"] = self.config.publisher_id
        url = f"{self.search_url}?{urllib.parse.urlencode(params)}"
        return self._request_json(url)

    def _request_json(self, url: str) -> dict:
        headers = self._signed_headers()
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            raise WalmartProviderError(f"Walmart API HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise WalmartProviderError(f"Walmart API network error: {exc.reason}") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WalmartProviderError("Walmart API returned non-JSON response.") from exc
        if not isinstance(decoded, dict):
            raise WalmartProviderError("Walmart API returned unexpected payload shape.")
        return decoded

    def _signed_headers(self) -> dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        key_version = self.config.key_version or "1"
        signature_payload = f"{self.config.consumer_id}\n{timestamp_ms}\n{key_version}\n".encode("utf-8")
        private_key = self._load_private_key()
        signature = private_key.sign(signature_payload, padding.PKCS1v15(), hashes.SHA256())
        signature_b64 = base64.b64encode(signature).decode("ascii")
        return {"Accept": "application/json", "WM_CONSUMER.ID": self.config.consumer_id or "", "WM_CONSUMER.INTIMESTAMP": timestamp_ms, "WM_SEC.KEY_VERSION": key_version, "WM_SEC.AUTH_SIGNATURE": signature_b64}

    def _load_private_key(self):
        if not self.config.private_key_b64:
            raise WalmartProviderError("Walmart private key is missing.")
        try:
            key_bytes = base64.b64decode(self.config.private_key_b64)
            return serialization.load_pem_private_key(key_bytes, password=None)
        except Exception as exc:
            raise WalmartProviderError("Walmart private key could not be decoded. Recreate WALMART_PRIVATE_KEY_B64.") from exc

    def _candidates_from_payload(self, payload: dict, request: ProviderScanRequest) -> list[SourceCandidate]:
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        candidates: list[SourceCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = self._candidate_from_item(item, request=request)
            if candidate:
                candidates.append(candidate)
        return candidates

    def _candidate_from_item(self, item: dict, request: ProviderScanRequest) -> SourceCandidate | None:
        title = str(item.get("name") or "").strip()
        raw_tracking_url = str(item.get("productTrackingUrl") or "").strip()
        item_id = item.get("itemId") or item.get("usItemId")
        direct_product_url = _direct_walmart_url(item_id)
        product_url = raw_tracking_url or direct_product_url
        signals = self._item_signals(item)
        if product_url and "|PUBID|" in product_url and direct_product_url:
            product_url = direct_product_url
            signals.append("tracking link unavailable; direct Walmart link used")
        if not title or not product_url:
            return None

        selected_offer = _selected_offer_proof(item)
        seller_name = selected_offer.get("seller_name")
        fulfillment_type = selected_offer.get("fulfillment_type")
        condition = selected_offer.get("condition")
        for signal in _seller_signals(seller_name=seller_name, fulfillment_type=fulfillment_type, condition=condition):
            signals.append(signal)

        current_price, current_price_signal = _trusted_current_price(item)
        if current_price_signal:
            signals.append(current_price_signal)
        typical_price, reference_signal = _trusted_reference_price(item=item, title=title, current_price=current_price)
        if reference_signal:
            signals.append(reference_signal)

        variant = extract_variant_proof(item, title)
        promotions = _walmart_promotion_proof(item)
        proof_attrs = _walmart_proof_attributes(item, variant.attributes, selected_offer, promotions)
        if typical_price is not None:
            proof_attrs["referencePriceTrusted"] = "yes"
        elif reference_signal and reference_signal.startswith("ignored"):
            proof_attrs["referencePriceTrusted"] = "no"
        if variant.warning:
            signals.append(variant.warning)
        elif variant.label:
            signals.append(f"selected option: {variant.label}")
        category_path = str(item.get("categoryPath") or "").strip()
        if category_path:
            proof_attrs["category"] = category_path
            signals.append(f"Walmart category: {category_path}")
        if promotions.get("couponSavings"):
            signals.append(f"Walmart coupon detected: ${float(promotions['couponSavings']):,.2f}")
        if promotions.get("walmartCashSavings"):
            signals.append(f"Walmart Cash detected: ${float(promotions['walmartCashSavings']):,.2f}")

        return SourceCandidate(
            source_key=self.provider_key,
            retailer="Walmart",
            title=title,
            product_url=product_url,
            current_price=current_price,
            typical_price=typical_price,
            image_url=str(item.get("largeImage") or item.get("mediumImage") or item.get("thumbnailImage") or "") or None,
            product_id=str(item_id) if item_id is not None else None,
            product_id_type="sku" if item_id is not None else None,
            sku=str(item_id) if item_id is not None else None,
            upc=str(item.get("upc")) if item.get("upc") else None,
            selected_offer_id=variant.offer_id,
            variant_label=variant.label,
            variant_attributes=proof_attrs,
            pack_size=proof_attrs.get("packSize") or proof_attrs.get("size") or proof_attrs.get("unitSize"),
            color=proof_attrs.get("color"),
            platform=proof_attrs.get("platform"),
            model=proof_attrs.get("model") or proof_attrs.get("modelNumber"),
            parent_title=title if proof_attrs else None,
            option_mismatch_warning=variant.warning,
            seller_name=seller_name,
            fulfillment_type=fulfillment_type,
            condition=condition,
            stock_status=str(item.get("stock") or "") or None,
            can_add_to_cart=bool(item.get("availableOnline")) if "availableOnline" in item else None,
            is_business_offer=False,
            is_member_only=False,
            is_checkout_price=False,
            signals=signals[:16],
        )

    def _item_signals(self, item: dict) -> list[str]:
        signals: list[str] = []
        if item.get("clearance") is True:
            signals.append("clearance")
        if item.get("rollBack") is True:
            signals.append("rollback")
        if item.get("specialBuy") is True:
            signals.append("special buy")
        if item.get("preOrder") is True:
            signals.append("preorder")
        if item.get("marketplace") is True:
            signals.append("marketplace seller")
        if item.get("bundle") is True:
            signals.append("bundle")
        if item.get("availableOnline") is False:
            signals.append("not available online")
        max_items = item.get("maxItemsInOrder")
        if max_items:
            signals.append(f"max order quantity: {max_items}")
        offer_type = item.get("offerType")
        if offer_type:
            signals.append(f"offer type: {offer_type}")
        if item.get("shipToStore") is True:
            signals.append("ship to store available")
        if item.get("freeShipToStore") is True:
            signals.append("free ship to store")
        if item.get("twoThreeDayShipping") is True:
            signals.append("2-3 day shipping")
        return signals

    def _missing_config(self) -> list[str]:
        missing: list[str] = []
        if not self.config.consumer_id:
            missing.append("WALMART_CONSUMER_ID")
        if not self.config.private_key_b64:
            missing.append("WALMART_PRIVATE_KEY_B64")
        return missing


class WalmartProviderError(RuntimeError):
    pass


def walmart_config_from_env(fallback_enabled: bool = False) -> WalmartAffiliateConfig:
    enabled_text = os.getenv("WALMART_PROVIDER_ENABLED", "").strip().lower()
    enabled = fallback_enabled if not enabled_text else enabled_text in {"1", "true", "yes", "on"}
    return WalmartAffiliateConfig(consumer_id=os.getenv("WALMART_CONSUMER_ID", "").strip() or None, key_version=os.getenv("WALMART_KEY_VERSION", "1").strip() or "1", private_key_b64=os.getenv("WALMART_PRIVATE_KEY_B64", "").strip() or None, publisher_id=os.getenv("WALMART_PUBLISHER_ID", "").strip() or None, enabled=enabled)


def _direct_walmart_url(item_id) -> str:
    if item_id is None or item_id == "":
        return ""
    return f"https://www.walmart.com/ip/{item_id}"


def _trusted_current_price(item: dict) -> tuple[float | None, str | None]:
    for source, value in _current_price_candidates(item):
        if value is not None and value >= 0:
            return value, f"Walmart current price source: {source}"
    return None, "Walmart current price missing"


def _current_price_candidates(item: dict) -> list[tuple[str, float | None]]:
    return [
        ("salePrice", _float_or_none(item.get("salePrice"))),
        ("currentPrice", _price_from_value(item.get("currentPrice"))),
        ("price", _price_from_value(item.get("price"))),
        ("priceInfo.currentPrice", _nested_price(item, "priceInfo", "currentPrice")),
        ("priceInfo.price", _nested_price(item, "priceInfo", "price")),
        ("minPrice", _float_or_none(item.get("minPrice"))),
    ]


def _trusted_reference_price(item: dict, title: str, current_price: float | None) -> tuple[float | None, str | None]:
    references = _reference_price_candidates(item)
    ignored: list[str] = []
    if current_price is None or current_price <= 0:
        value, source = _first_trusted_reference(references, title=title, current_price=current_price)
        return value, f"Walmart reference price source: {source}" if value and source else None
    for source, value in references:
        if value is None or value <= current_price:
            continue
        trust = _reference_price_trust(source)
        if trust != "high":
            ignored.append(f"{source}=${value:,.2f}")
            continue
        if _reference_price_looks_suspicious(source=source, title=title, current_price=current_price, reference_price=value):
            ignored.append(f"suspicious {source}=${value:,.2f}")
            continue
        return value, f"Walmart reference price source: {source}"
    if ignored:
        return None, "ignored low-confidence Walmart reference price(s): " + ", ".join(ignored[:3])
    return None, None


def _reference_price_candidates(item: dict) -> list[tuple[str, float | None]]:
    # Only high-trust sale/reference fields can prove true discounts. MSRP/list
    # values are stored as proof attributes but not used to calculate % off.
    references = [
        ("wasPrice", _price_from_value(item.get("wasPrice"))),
        ("priceInfo.wasPrice", _nested_price(item, "priceInfo", "wasPrice")),
        ("regularPrice", _price_from_value(item.get("regularPrice"))),
        ("strikeThroughPrice", _price_from_value(item.get("strikeThroughPrice"))),
        ("priceInfo.strikeThroughPrice", _nested_price(item, "priceInfo", "strikeThroughPrice")),
        ("comparisonPrice", _price_from_value(item.get("comparisonPrice"))),
        ("priceInfo.comparisonPrice", _nested_price(item, "priceInfo", "comparisonPrice")),
        ("listPrice", _price_from_value(item.get("listPrice"))),
        ("priceInfo.listPrice", _nested_price(item, "priceInfo", "listPrice")),
        ("msrp", _float_or_none(item.get("msrp"))),
    ]
    references.extend(_best_marketplace_reference_prices(item))
    return references


def _reference_price_trust(source: str) -> str:
    source_key = source.lower()
    if any(token in source_key for token in ("wasprice", "regularprice", "strikethrough", "comparisonprice")):
        return "high"
    return "low"


def _best_marketplace_reference_prices(item: dict) -> list[tuple[str, float | None]]:
    best_marketplace = item.get("bestMarketplacePrice")
    if not isinstance(best_marketplace, dict):
        return []
    return [("bestMarketplacePrice.price", _float_or_none(best_marketplace.get("price")))]


def _first_trusted_reference(references: list[tuple[str, float | None]], *, title: str, current_price: float | None) -> tuple[float | None, str | None]:
    for source, value in references:
        if not value or value <= 0:
            continue
        if _reference_price_trust(source) != "high":
            continue
        if current_price is not None and _reference_price_looks_suspicious(source=source, title=title, current_price=current_price, reference_price=value):
            continue
        return value, source
    return None, None


def _walmart_promotion_proof(item: dict[str, Any]) -> dict[str, str]:
    coupon = _promotion_amount(item, include_terms=("coupon",), exclude_terms=("cash", "reward"))
    walmart_cash = _promotion_amount(item, include_terms=("walmart cash", "cash reward", "reward"), exclude_terms=())
    attrs: dict[str, str] = {}
    if coupon and coupon > 0:
        attrs["couponSavings"] = f"{coupon:.2f}"
    if walmart_cash and walmart_cash > 0:
        attrs["walmartCashSavings"] = f"{walmart_cash:.2f}"
    return attrs


def _promotion_amount(value: Any, *, include_terms: tuple[str, ...], exclude_terms: tuple[str, ...]) -> float | None:
    best: float | None = None
    for key_path, candidate in _walk_payload(value):
        lowered_key = key_path.lower()
        lowered_text = str(candidate).lower()
        haystack = f"{lowered_key} {lowered_text}"
        if not any(term in haystack for term in include_terms):
            continue
        if any(term in haystack for term in exclude_terms):
            continue
        parsed = _price_from_value(candidate)
        if parsed is None:
            parsed = _first_money_amount(str(candidate))
        if parsed is None or parsed <= 0:
            continue
        best = max(best or 0, parsed)
    return best


def _walk_payload(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_payload(child, child_prefix)
        return
    if isinstance(value, list):
        for idx, child in enumerate(value):
            child_prefix = f"{prefix}[{idx}]"
            yield from _walk_payload(child, child_prefix)
        return
    yield prefix, value


def _first_money_amount(text: str) -> float | None:
    match = re.search(r"\$\s*(\d+(?:\.\d{1,2})?)", text)
    if not match:
        return None
    return _float_or_none(match.group(1))


def _selected_offer_proof(item: dict[str, Any]) -> dict[str, str | None]:
    seller_name = _clean_string(
        item.get("sellerName")
        or item.get("sellerDisplayName")
        or item.get("seller")
        or _nested_value(item, "sellerInfo", "sellerName")
        or _nested_value(item, "sellerInfo", "name")
        or _nested_value(item, "seller", "name")
    )
    seller_id = _clean_string(
        item.get("sellerId")
        or item.get("sellerID")
        or _nested_value(item, "sellerInfo", "sellerId")
        or _nested_value(item, "seller", "id")
    )
    fulfillment_type = _clean_string(
        item.get("fulfillmentType")
        or item.get("fulfillment")
        or item.get("fulfillmentBadge")
        or _nested_value(item, "fulfillmentSummary", "fulfillment")
        or _nested_value(item, "fulfillmentSummary", "fulfillmentType")
    )
    condition = _clean_string(item.get("condition") or item.get("conditionType") or _nested_value(item, "condition", "type"))
    is_walmart_seller = _is_walmart_seller(seller_name=seller_name, seller_id=seller_id, item=item)
    return {
        "seller_name": seller_name or ("Walmart" if is_walmart_seller else None),
        "seller_id": seller_id,
        "fulfillment_type": fulfillment_type,
        "condition": condition,
        "is_walmart_seller": "yes" if is_walmart_seller else "no" if seller_name or seller_id or item.get("marketplace") is True else None,
    }


def _seller_signals(*, seller_name: str | None, fulfillment_type: str | None, condition: str | None) -> list[str]:
    signals: list[str] = []
    if seller_name:
        signals.append(f"selected offer seller: {seller_name}")
        if not _seller_name_is_walmart(seller_name):
            signals.append("selected offer may be third-party seller")
    if fulfillment_type:
        signals.append(f"fulfillment: {fulfillment_type}")
    if condition:
        signals.append(f"condition: {condition}")
    return signals


def _is_walmart_seller(*, seller_name: str | None, seller_id: str | None, item: dict[str, Any]) -> bool:
    if _seller_name_is_walmart(seller_name):
        return True
    if seller_id and seller_id.strip().upper() in {"0", "F55CDC31AB754BB68FE0B39041159D63", "WALMART"}:
        return True
    if item.get("marketplace") is False and not seller_name:
        return True
    return False


def _seller_name_is_walmart(seller_name: str | None) -> bool:
    if not seller_name:
        return False
    return seller_name.strip().lower() in {"walmart", "walmart.com", "walmart stores, inc.", "walmart stores inc"}


def _walmart_proof_attributes(item: dict[str, Any], variant_attrs: dict[str, str], selected_offer: dict[str, str | None] | None = None, promotions: dict[str, str] | None = None) -> dict[str, str]:
    attrs: dict[str, str] = dict(variant_attrs)
    for key, label in (
        ("brandName", "brand"),
        ("manufacturer", "manufacturer"),
        ("modelNumber", "modelNumber"),
        ("msrp", "msrp"),
        ("customerRating", "rating"),
        ("numReviews", "reviews"),
        ("offerType", "offerType"),
        ("productUrlText", "urlText"),
        ("categoryNode", "categoryNode"),
        ("unitPrice", "unitPrice"),
        ("unit", "unit"),
        ("size", "size"),
        ("color", "color"),
    ):
        value = _clean_string(item.get(key))
        if value and label not in attrs:
            attrs[label] = value
    if promotions:
        attrs.update(promotions)
    if selected_offer:
        for key, label in (("seller_name", "seller"), ("seller_id", "sellerId"), ("fulfillment_type", "fulfillment"), ("condition", "condition"), ("is_walmart_seller", "walmartSeller")):
            value = selected_offer.get(key)
            if value:
                attrs[label] = value
    for key in ("rollback", "clearance", "specialBuy", "marketplace", "bundle", "availableOnline", "shipToStore", "freeShipToStore", "twoThreeDayShipping"):
        if key in item:
            attrs[key] = "yes" if item.get(key) is True else "no"
    max_items = _clean_string(item.get("maxItemsInOrder"))
    if max_items:
        attrs["maxOrderQty"] = max_items
    unit_size = _unit_size_from_title(str(item.get("name") or ""))
    if unit_size and "unitSize" not in attrs:
        attrs["unitSize"] = unit_size
    return attrs


def _unit_size_from_title(title: str) -> str | None:
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(fl\s*oz|fluid\s*ounce|oz|ounce|ounces|ct|count|lb|lbs|pack)\b", title, flags=re.IGNORECASE)
    if not match:
        return None
    amount, unit = match.groups()
    normalized_unit = unit.lower().replace(" ", "")
    if normalized_unit in {"floz", "fluidounce", "ounce", "ounces"}:
        normalized_unit = "oz"
    return f"{amount} {normalized_unit}"


def _clean_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dict):
        return _clean_string(_price_from_value(value))
    return str(value).strip() or None


def _nested_value(item: dict[str, Any], *path: str) -> Any:
    value: Any = item
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _nested_price(item: dict, *path: str) -> float | None:
    value = _nested_value(item, *path)
    return _price_from_value(value)


def _price_from_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("price", "amount", "value", "displayValue"):
            parsed = _float_or_none(value.get(key))
            if parsed is not None:
                return parsed
        return None
    return _float_or_none(value)


def _reference_price_looks_suspicious(*, source: str, title: str, current_price: float, reference_price: float) -> bool:
    ratio = reference_price / current_price
    if ratio <= 1:
        return True

    source_key = source.lower()
    title_text = title.lower()
    explicit_sale_source = any(token in source_key for token in ("wasprice", "regularprice", "strikethrough", "comparisonprice"))

    if _is_consumable_or_size_sensitive(title_text):
        if ratio >= 8:
            return True
        if current_price <= 15 and reference_price >= 50 and not explicit_sale_source:
            return True
    if current_price <= 10 and reference_price >= 150:
        return True
    if ratio >= 20 and not explicit_sale_source:
        return True
    return False


def _is_consumable_or_size_sensitive(title: str) -> bool:
    keywords = (
        "toilet paper", "toilet tissue", "bath tissue", "paper towel", "paper towels", "tissue", "napkin",
        "detergent", "laundry", "trash bag", "dish soap", "cleaner", "cleaning", "wipes", "diaper", "razor",
        "disposable", "shampoo", "conditioner", "body wash", "soap", "toothpaste", "toothbrush", "deodorant",
        "car wash", "wash", "wax", "turtle wax", "armor all", "meguiar", "chemical guys", "spray", "fluid",
        "oz", "fl oz", "ounce", "count", "ct", "pack", "refill", "bottle", "jug", "gallon",
    )
    return any(keyword in title for keyword in keywords)


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
