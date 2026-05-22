from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import (
    DealProvider,
    ProviderCapability,
    ProviderHealth,
    ProviderScanRequest,
    ProviderScanResult,
    ProviderStatus,
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
    """Walmart Affiliate API adapter.

    The provider stays disabled unless WALMART_PROVIDER_ENABLED is explicitly
    enabled and signing credentials are configured. It returns SourceCandidate
    objects only; it never posts alerts directly.
    """

    provider_key = "walmart"
    display_name = "Walmart"
    search_url = "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/search"
    taxonomy_url = "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/taxonomy"
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
            config = WalmartAffiliateConfig(enabled=bool(configured))
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

        return ProviderHealth(
            provider_key=self.provider_key,
            ok=True,
            status=ProviderStatus.READY,
            message="Ready: Walmart Affiliate API credentials are configured.",
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
            )

        warnings: list[str] = []
        candidates: list[SourceCandidate] = []

        queries = [request.query] if request.query else []
        queries.extend(request.product_ids)
        for query in queries:
            if not query:
                continue
            try:
                payload = self._search(query=query, max_results=request.max_results)
            except WalmartProviderError as exc:
                warnings.append(str(exc))
                continue
            candidates.extend(self._candidates_from_payload(payload, request=request))

        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=tuple(candidates),
            warnings=tuple(warnings),
        )

    def _search(self, query: str, max_results: int) -> dict:
        params = {
            "query": query,
            "numItems": str(max(1, min(max_results, 25))),
            "responseGroup": "full",
        }
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
        except Exception as exc:  # noqa: BLE001 - keep credential parsing failures user-actionable.
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
        product_url = str(item.get("productTrackingUrl") or "").strip()
        item_id = item.get("itemId")
        if not title or not product_url:
            return None

        signals = self._item_signals(item)
        category_path = str(item.get("categoryPath") or "").strip()
        if category_path:
            signals.append(f"Walmart category: {category_path}")

        return SourceCandidate(
            source_key=self.provider_key,
            retailer="Walmart",
            title=title,
            product_url=product_url,
            current_price=_float_or_none(item.get("salePrice")),
            typical_price=_float_or_none(item.get("msrp")) or _float_or_none(item.get("listPrice")),
            image_url=str(item.get("largeImage") or item.get("mediumImage") or item.get("thumbnailImage") or "") or None,
            product_id=str(item_id) if item_id is not None else None,
            product_id_type="sku" if item_id is not None else None,
            sku=str(item_id) if item_id is not None else None,
            upc=str(item.get("upc")) if item.get("upc") else None,
            stock_status=str(item.get("stock") or "") or None,
            can_add_to_cart=bool(item.get("availableOnline")) if "availableOnline" in item else None,
            is_business_offer=False,
            is_member_only=False,
            is_checkout_price=False,
            signals=signals[:8],
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


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
