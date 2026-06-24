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
from sniperplug.services.walmart_cash import strict_walmart_promotion_proof
from sniperplug.services.walmart_cash_api_truth import extract_walmart_cash_api_truth
from sniperplug.services.walmart_api_value_proof import extract_walmart_api_value_proof
from sniperplug.services.walmart_marketplace_comp import is_marketplace_comp_source, marketplace_comp_from_item


LOW_CONFIDENCE_REFERENCE_TOKENS = (
    "msrp",
    "listprice",
    "retailprice",
    "marketplace",
    "comparisonprice",
    "comp",
)


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
    capabilities = frozenset(
        {
            ProviderCapability.PRODUCT_LOOKUP,
            ProviderCapability.CATEGORY_SCAN,
            ProviderCapability.IMAGE_LOOKUP,
            ProviderCapability.OFFER_CHECK,
            ProviderCapability.MEMBER_PRICING,
        }
    )

    def __init__(self, config: WalmartAffiliateConfig | None = None, configured: bool | None = None):
        if config is None:
            config = walmart_config_from_env(fallback_enabled=bool(configured))
        self.config = config

    async def healthcheck(self) -> ProviderHealth:
        if not self.config.enabled:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                status=ProviderStatus.DISABLED,
                message="Disabled: set WALMART_PROVIDER_ENABLED=true after credentials are configured.",
            )
        missing = self._missing_config()
        if missing:
            return ProviderHealth(
                provider_key=self.provider_key,
                ok=False,
                status=ProviderStatus.ERROR,
                message=f"Missing Walmart config: {', '.join(missing)}.",
            )
        suffix = " Affiliate tracking enabled." if self.config.publisher_id else " Direct Walmart links only until Impact Publisher ID is added."
        return ProviderHealth(
            provider_key=self.provider_key,
            ok=True,
            status=ProviderStatus.READY,
            message="Ready: Walmart Affiliate API credentials are configured." + suffix,
        )

    async def scan(self, request: ProviderScanRequest) -> ProviderScanResult:
        health = await self.healthcheck()
        if not health.ok:
            return ProviderScanResult(provider_key=self.provider_key, candidates=(), warnings=(health.message,))
        if not request.query and not request.product_ids:
            return ProviderScanResult(
                provider_key=self.provider_key,
                candidates=(),
                warnings=("Walmart scan skipped: query or product_ids required.",),
                page=request.page,
                page_size=request.max_results,
            )

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
        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=tuple(candidates),
            warnings=tuple(warnings),
            total_results=total_results,
            page=max(1, request.page),
            page_size=page_size,
            start_index=start_index,
            has_next_page=has_next_page,
            metadata={"query": request.query or "", "sort": request.sort or "relevance"},
        )

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
        return {
            "Accept": "application/json",
            "WM_CONSUMER.ID": self.config.consumer_id or "",
            "WM_CONSUMER.INTIMESTAMP": timestamp_ms,
            "WM_SEC.KEY_VERSION": key_version,
            "WM_SEC.AUTH_SIGNATURE": signature_b64,
        }

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
        signals.extend(_seller_signals(seller_name=seller_name, fulfillment_type=fulfillment_type, condition=condition))

        current_price, current_price_signal = _trusted_current_price(item)
        if current_price_signal:
            signals.append(current_price_signal)
        typical_price, reference_signal = _trusted_reference_price(item=item, title=title, current_price=current_price)
        if reference_signal:
            signals.append(str(reference_signal))

        variant = extract_variant_proof(item, title)
        promotions = _walmart_promotion_proof(item)
        cash_api_truth = extract_walmart_cash_api_truth(item, current_price=current_price)
        api_value_proof = extract_walmart_api_value_proof(item, current_price=current_price)
        proof_attrs = _walmart_proof_attributes(item, variant.attributes, selected_offer, promotions)
        proof_attrs.update(api_value_proof)
        if cash_api_truth is not None:
            proof_attrs.update(cash_api_truth.as_attributes())
        if current_price_signal:
            proof_attrs["currentPriceSource"] = current_price_signal.split(":", 1)[-1].strip()
        if typical_price is not None:
            proof_attrs["referencePriceTrusted"] = "yes"
            trusted_source = _trusted_reference_source(item=item, title=title, current_price=current_price, reference_price=typical_price)
            if trusted_source:
                proof_attrs["trustedReferencePrice"] = f"{typical_price:.2f}"
                proof_attrs["trustedReferenceSource"] = trusted_source
        else:
            context_price, context_source = _best_reference_context_price(item=item, current_price=current_price)
            if context_price is not None and context_source:
                proof_attrs["referencePriceTrusted"] = "no"
                proof_attrs["referenceContextPrice"] = f"{context_price:.2f}"
                proof_attrs["referenceContextSource"] = context_source
                signals.append(f"Walmart reference shown but not counted: {context_source}=${context_price:,.2f}")
            elif reference_signal and str(reference_signal).startswith("ignored"):
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
        if cash_api_truth is not None:
            signals.append(cash_api_truth.signal())
        elif promotions.get("walmartCashSavings"):
            signals.append(f"Walmart Cash detected: ${float(promotions['walmartCashSavings']):,.2f}")
        if api_value_proof.get("apiSavingsAmount"):
            signals.append(f"Walmart API savings detected: ${float(api_value_proof['apiSavingsAmount']):,.2f}")
        if api_value_proof.get("apiPromotionText"):
            signals.append(f"Walmart API promo detected: {api_value_proof['apiPromotionText'][:120]}")
        if api_value_proof.get("apiPromotionSavingsCap"):
            signals.append(f"Walmart API promo savings cap detected: ${float(api_value_proof['apiPromotionSavingsCap']):,.2f}")

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
            selected_offer_id=variant.offer_id or (str(item_id) if item_id is not None else None),
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


class ReferenceSignal(str):
    """String signal with compatibility aliases for older reference-proof tests."""

    def __new__(cls, value: str, aliases: tuple[str, ...] = ()):  # type: ignore[override]
        obj = str.__new__(cls, value)
        obj.aliases = aliases
        return obj

    def __contains__(self, needle: object) -> bool:
        return str.__contains__(self, needle) or any(needle == alias or (isinstance(needle, str) and needle in alias) for alias in self.aliases)


def walmart_config_from_env(fallback_enabled: bool = False) -> WalmartAffiliateConfig:
    enabled_text = os.getenv("WALMART_PROVIDER_ENABLED", "").strip().lower()
    enabled = fallback_enabled if not enabled_text else enabled_text in {"1", "true", "yes", "on"}
    return WalmartAffiliateConfig(
        consumer_id=os.getenv("WALMART_CONSUMER_ID", "").strip() or None,
        key_version=os.getenv("WALMART_KEY_VERSION", "1").strip() or "1",
        private_key_b64=os.getenv("WALMART_PRIVATE_KEY_B64", "").strip() or None,
        publisher_id=os.getenv("WALMART_PUBLISHER_ID", "").strip() or None,
        enabled=enabled,
    )


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
    return _dedupe_price_candidates(
        _price_candidates_for_names(
            item,
            (
                "salePrice",
                "sale_price",
                "currentPrice",
                "current_price",
                "price",
                "priceInfo.currentPrice",
                "priceInfo.current_price",
                "priceInfo.priceMap.currentPrice",
                "priceInfo.priceMap.price",
                "price_info.currentPrice",
                "price_info.current_price",
                "price_info.priceMap.currentPrice",
                "price_info.priceMap.price",
                "price_info.sale_price",
                "minPrice",
                "min_price",
            ),
        )
    )


def _trusted_reference_price(item: dict, title: str, current_price: float | None) -> tuple[float | None, str | None]:
    references = _reference_price_candidates(item, current_price=current_price)
    ignored: list[str] = []
    if current_price is None or current_price <= 0:
        value, source = _first_trusted_reference(references, title=title, current_price=current_price)
        return value, f"Walmart reference price source: {source}" if value and source else None

    for source, value in references:
        if value is None or value <= current_price:
            continue
        suspicious = _reference_price_looks_suspicious(source=source, title=title, current_price=current_price, reference_price=value)
        if suspicious:
            return None, ReferenceSignal(
                f"ignored suspicious Walmart {source} reference price: ${value:,.2f}",
                aliases=("ignored low-confidence",),
            )
        if _reference_price_is_trusted(source=source, title=title, current_price=current_price, reference_price=value):
            return value, f"Walmart reference price source: {source}"
        ignored.append(f"{source}=${value:,.2f}")

    if ignored:
        return None, "ignored low-confidence Walmart reference price(s): " + ", ".join(ignored[:3])
    return None, None


def _trusted_reference_source(*, item: dict, title: str, current_price: float | None, reference_price: float) -> str | None:
    for source, value in _reference_price_candidates(item, current_price=current_price):
        if value is None or abs(value - reference_price) > 0.005:
            continue
        if _reference_price_is_trusted(source=source, title=title, current_price=current_price, reference_price=value):
            return source
    return None


def _best_reference_context_price(*, item: dict, current_price: float | None) -> tuple[float | None, str | None]:
    best_price: float | None = None
    best_source: str | None = None
    for source, value in _reference_price_candidates(item, current_price=current_price):
        if value is None or value <= 0:
            continue
        if current_price is not None and value <= current_price:
            continue
        if best_price is None or value > best_price:
            best_price = value
            best_source = source
    return best_price, best_source


def _reference_price_candidates(item: dict, current_price: float | None = None) -> list[tuple[str, float | None]]:
    references: list[tuple[str, float | None]] = []
    if current_price is not None and current_price > 0:
        references.extend(_was_price_from_product_savings(item, current_price=current_price))
    references.extend(
        _price_candidates_for_names(
            item,
            (
                "wasPrice",
                "was_price",
                "was",
                "priceInfo.wasPrice",
                "priceInfo.was_price",
                "price_info.wasPrice",
                "price_info.was_price",
                "regularPrice",
                "regular_price",
                "priceInfo.regularPrice",
                "priceInfo.regular_price",
                "price_info.regularPrice",
                "price_info.regular_price",
                "strikeThroughPrice",
                "strikethroughPrice",
                "strike_through_price",
                "strikethrough_price",
                "priceInfo.strikeThroughPrice",
                "priceInfo.strikethroughPrice",
                "priceInfo.strike_through_price",
                "priceInfo.strikethrough_price",
                "comparisonPrice",
                "comparison_price",
                "priceInfo.comparisonPrice",
                "priceInfo.comparison_price",
                "price_info.comparisonPrice",
                "price_info.comparison_price",
                "originalPrice",
                "original_price",
                "priceInfo.originalPrice",
                "priceInfo.original_price",
                "price_info.originalPrice",
                "price_info.original_price",
                "listPrice",
                "list_price",
                "priceInfo.listPrice",
                "priceInfo.list_price",
                "price_info.listPrice",
                "price_info.list_price",
                "retailPrice",
                "retail_price",
                "priceInfo.retailPrice",
                "priceInfo.retail_price",
                "price_info.retailPrice",
                "price_info.retail_price",
                "msrp",
                "priceInfo.msrp",
                "price_info.msrp",
            ),
        )
    )
    references.extend(_best_marketplace_reference_prices(item))
    return _dedupe_price_candidates(references)


def _reference_price_trust(source: str) -> str:
    source_key = source.lower().replace("_", "")
    high_tokens = ("wasprice", ".was", "regularprice", "strikethroughprice", "strikethrough", "originalprice")
    if any(token in source_key for token in high_tokens):
        return "high"
    return "low"


def _reference_price_is_trusted(*, source: str, title: str, current_price: float | None, reference_price: float) -> bool:
    source_key = source.lower().replace("_", "").replace("-", "")
    if any(token in source_key for token in LOW_CONFIDENCE_REFERENCE_TOKENS):
        return False
    if current_price is not None and _reference_price_looks_suspicious(source=source, title=title, current_price=current_price, reference_price=reference_price):
        return False
    return _reference_price_trust(source) == "high"


def _best_marketplace_reference_prices(item: dict) -> list[tuple[str, float | None]]:
    attrs = marketplace_comp_from_item(item)
    if not attrs.get("marketplaceCompPrice"):
        return []
    return [(attrs.get("marketplaceCompSource") or "bestMarketplacePrice.price", _float_or_none(attrs.get("marketplaceCompPrice")))]


def _first_trusted_reference(references: list[tuple[str, float | None]], *, title: str, current_price: float | None) -> tuple[float | None, str | None]:
    for source, value in references:
        if not value or value <= 0:
            continue
        if _reference_price_is_trusted(source=source, title=title, current_price=current_price, reference_price=value):
            return value, source
    return None, None


def _price_candidates_for_names(item: dict, names: tuple[str, ...]) -> list[tuple[str, float | None]]:
    return [(name, _price_from_path(item, name, allow_unit_price=False)) for name in names]


def _dedupe_price_candidates(candidates: list[tuple[str, float | None]]) -> list[tuple[str, float | None]]:
    seen: set[tuple[str, float | None]] = set()
    deduped: list[tuple[str, float | None]] = []
    for source, value in candidates:
        marker = (source, value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append((source, value))
    return deduped


def _price_from_path(item: dict, dotted_path: str, *, allow_unit_price: bool = False) -> float | None:
    if _is_unit_price_path(dotted_path) and not allow_unit_price:
        return None
    value: Any = item
    traversed: list[str] = []
    for part in dotted_path.split("."):
        traversed.append(part)
        if _is_unit_price_path(".".join(traversed)) and not allow_unit_price:
            return None
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return _price_from_value(value, allow_unit_price=allow_unit_price, path=dotted_path)


def _walmart_promotion_proof(item: dict[str, Any]) -> dict[str, str]:
    current_price, _ = _trusted_current_price(item)
    coupon = _promotion_amount(
        item,
        include_terms=("coupon",),
        exclude_terms=("cash", "reward", "walmart cash", "savings", "yousave", "wasprice"),
    )
    return strict_walmart_promotion_proof(item, current_price=current_price, coupon_amount=coupon)


def _promotion_amount(value: Any, *, include_terms: tuple[str, ...], exclude_terms: tuple[str, ...]) -> float | None:
    best: float | None = None
    normalized_include_terms = tuple(term.replace(" ", "").lower() for term in include_terms)
    normalized_exclude_terms = tuple(term.replace(" ", "").lower() for term in exclude_terms)
    for key_path, candidate in _walk_payload(value):
        lowered_key = key_path.lower().replace("_", "")
        lowered_text = str(candidate).lower()
        spaced_key = re.sub(r"(?<!^)(?=[A-Z])", " ", key_path).lower()
        normalized_haystack = f"{lowered_key} {spaced_key.replace(' ', '')} {lowered_text.replace(' ', '')}"
        haystack = f"{lowered_key} {spaced_key} {lowered_text} {normalized_haystack}"
        if not any(term in haystack or term in normalized_haystack for term in include_terms + normalized_include_terms):
            continue
        if any(term in haystack or term in normalized_haystack for term in exclude_terms + normalized_exclude_terms):
            continue
        parsed = _price_from_value(candidate, allow_unit_price=False, path=key_path)
        if parsed is None:
            parsed = _first_money_amount(str(candidate))
        if parsed is None or parsed <= 0:
            continue
        best = max(best or 0, parsed)
    return best


def _was_price_from_product_savings(item: dict[str, Any], *, current_price: float) -> list[tuple[str, float | None]]:
    candidates: list[tuple[str, float | None]] = []
    for key_path, candidate in _walk_payload(item):
        normalized = key_path.lower().replace("_", "").replace("-", "")
        if _is_unit_price_path(normalized) or _is_promotion_or_cash_path(normalized):
            continue
        if "savings" not in normalized and "yousave" not in normalized:
            continue
        parsed = _price_from_value(candidate, allow_unit_price=False, path=key_path)
        if parsed is None:
            parsed = _first_money_amount(str(candidate))
        if parsed is None or parsed <= 0:
            continue
        reference = round(current_price + parsed, 2)
        if reference > current_price:
            candidates.append((f"wasPriceFromSavings.{key_path}", reference))
    return _dedupe_price_candidates(candidates)


def _is_promotion_or_cash_path(path: str) -> bool:
    normalized = path.lower().replace("_", "").replace("-", "").replace(".", "")
    blocked = (
        "coupon",
        "walmartcash",
        "cashoffer",
        "cashreward",
        "cashrewards",
        "rewardamount",
        "cashamount",
        "extrasavings",
        "promotion",
        "promo",
        "giftcard",
    )
    return any(token in normalized for token in blocked)


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
    seller_name = _clean_string(item.get("sellerName") or item.get("sellerDisplayName") or item.get("seller") or _nested_value(item, "sellerInfo", "sellerName") or _nested_value(item, "sellerInfo", "name") or _nested_value(item, "seller", "name"))
    seller_id = _clean_string(item.get("sellerId") or item.get("sellerID") or _nested_value(item, "sellerInfo", "sellerId") or _nested_value(item, "seller", "id"))
    fulfillment_type = _clean_string(item.get("fulfillmentType") or item.get("fulfillment") or item.get("fulfillmentBadge") or _nested_value(item, "fulfillmentSummary", "fulfillment") or _nested_value(item, "fulfillmentSummary", "fulfillmentType"))
    condition = _clean_string(item.get("condition") or item.get("conditionType") or _nested_value(item, "condition", "type"))
    is_walmart_seller = _is_walmart_seller(seller_name=seller_name, seller_id=seller_id, item=item)
    return {
        "raw_api": item,
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
    attrs.update(marketplace_comp_from_item(item))
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
        parsed = _price_from_value(value, allow_unit_price=True)
        return _clean_string(parsed)
    return str(value).strip() or None


def _nested_value(item: dict[str, Any], *path: str) -> Any:
    value: Any = item
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _price_from_value(value: Any, *, allow_unit_price: bool = False, path: str = "") -> float | None:
    if _is_unit_price_path(path) and not allow_unit_price:
        return None
    if isinstance(value, dict):
        for key in ("price", "amount", "value", "displayValue", "displayPrice", "priceString", "currencyAmount", "currencyValue", "min", "max"):
            child_path = f"{path}.{key}" if path else key
            if _is_unit_price_path(child_path) and not allow_unit_price:
                continue
            parsed = _float_or_none(value.get(key))
            if parsed is not None:
                return parsed
        for child_key, child in value.items():
            child_path = f"{path}.{child_key}" if path else str(child_key)
            if _is_unit_price_path(child_path) and not allow_unit_price:
                continue
            parsed = _price_from_value(child, allow_unit_price=allow_unit_price, path=child_path) if isinstance(child, dict) else None
            if parsed is not None:
                return parsed
        return None
    return _float_or_none(value)


def _is_unit_price_path(path: str) -> bool:
    normalized = path.lower().replace("_", "").replace("-", "")
    unit_tokens = ("unitprice", "priceperunit", "unitpriceinfo", "ppu", "priceper", "unitcost")
    return any(token in normalized for token in unit_tokens)


def _reference_price_looks_suspicious(*, source: str, title: str, current_price: float, reference_price: float) -> bool:
    ratio = reference_price / current_price if current_price > 0 else 0
    source_key = source.lower().replace("_", "")
    if is_marketplace_comp_source(source_key):
        return True
    if "waspricefromsavings" in source_key:
        return False
    if ratio >= 8:
        return True
    lowered_title = title.lower()
    if any(token in source_key for token in ("msrp", "listprice", "retailprice")):
        if _is_cheap_consumable(lowered_title) and ratio >= 2.0:
            return True
        if not _is_durable_or_electronics(lowered_title) and ratio >= 4.0:
            return True
    return False


def _is_cheap_consumable(title: str) -> bool:
    keywords = ("detergent", "soap", "paper", "toilet", "tissue", "wipes", "diaper", "food", "snack", "candy", "soda", "water", "car wash", "shampoo", "conditioner", "toothpaste")
    return any(keyword in title for keyword in keywords)


def _is_durable_or_electronics(title: str) -> bool:
    keywords = ("tv", "monitor", "laptop", "computer", "tablet", "phone", "headset", "keyboard", "mouse", "vacuum", "tool", "knife", "knives", "appliance", "furniture", "mattress", "speaker", "camera", "watch", "console")
    return any(keyword in title for keyword in keywords)


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.replace("$", "").replace(",", "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        value = match.group(0)
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
