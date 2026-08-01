from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from sniperplug.services.walmart_product_metadata import extract_walmart_product_metadata


_PATCH_FLAG = "_sniperplug_product_metadata_installed"
_ORIGINAL_ATTR = "_sniperplug_product_metadata_original_builder"


def install_walmart_product_metadata(provider: Any) -> Any:
    """Attach retailer-wide metadata extraction to the concrete Walmart provider.

    CachedWalmartProvider exposes the concrete provider through ``inner``. The
    patch is installed once per provider instance and wraps the same candidate
    builder used by search and exact-item detail enrichment.
    """

    if str(getattr(provider, "provider_key", "") or "").strip().lower() != "walmart":
        return provider

    target = getattr(provider, "inner", provider)
    if bool(getattr(target, _PATCH_FLAG, False)):
        return provider

    original = getattr(target, "_candidate_from_item", None)
    if not callable(original):
        return provider

    @wraps(original)
    def metadata_candidate_builder(item: dict, request: Any):
        candidate = original(item, request=request)
        if candidate is None or not isinstance(item, dict):
            return candidate

        metadata_map = getattr(request, "metadata", None)
        request_metadata = metadata_map if isinstance(metadata_map, dict) else {}
        exact_detail = str(
            request_metadata.get("exact_detail_price_check")
            or request_metadata.get("exact_detail")
            or ""
        ).strip().lower() in {"1", "true", "yes", "on"}

        metadata = extract_walmart_product_metadata(
            item,
            current_price=getattr(candidate, "api_current_price", None)
            or getattr(candidate, "current_price", None),
            reference_price=getattr(candidate, "api_reference_price", None)
            or getattr(candidate, "typical_price", None),
            exact_detail=exact_detail,
        )

        attrs = dict(getattr(candidate, "variant_attributes", None) or {})
        attrs.update(metadata.attributes)
        candidate.variant_attributes = attrs

        signals: list[str] = []
        for signal in (
            *list(getattr(candidate, "signals", ()) or ()),
            *metadata.signals,
        ):
            text = " ".join(str(signal or "").split())
            if text and text not in signals:
                signals.append(text)
        candidate.signals = signals[:24]
        return candidate

    setattr(target, _ORIGINAL_ATTR, original)
    setattr(target, "_candidate_from_item", metadata_candidate_builder)
    setattr(target, _PATCH_FLAG, True)
    return provider


def walmart_product_metadata_installed(provider: Any) -> bool:
    target = getattr(provider, "inner", provider)
    return bool(getattr(target, _PATCH_FLAG, False))
