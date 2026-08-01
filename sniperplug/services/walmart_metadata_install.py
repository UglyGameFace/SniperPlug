from __future__ import annotations

from functools import wraps
from typing import Any

from sniperplug.services.walmart_product_metadata import extract_walmart_product_metadata


_PATCH_FLAG = "_sniperplug_product_metadata_installed"
_ORIGINAL_ATTR = "_sniperplug_product_metadata_original_builder"
_REVIEW_PATCH_FLAG = "_sniperplug_walmart_metadata_bridge_installed"
_RENDERER_PATCH_FLAG = "_sniperplug_walmart_metadata_lines_installed"
_EXACT_DETAIL_MARKERS = {
    "1",
    "true",
    "yes",
    "on",
    "queue",
    "queued",
    "exact",
    "detail",
    "recheck",
    "verification_queue",
}


def install_walmart_product_metadata(provider: Any) -> Any:
    """Attach retailer-wide metadata extraction to every Walmart card path.

    CachedWalmartProvider exposes the concrete provider through ``inner``. The
    candidate builder used by both search and exact detail is wrapped once per
    provider instance. Separate module-level bridges preserve the structured
    attributes on private review cards and render the same facts on public cards.
    """

    if str(getattr(provider, "provider_key", "") or "").strip().lower() != "walmart":
        return provider

    target = getattr(provider, "inner", provider)
    if not bool(getattr(target, _PATCH_FLAG, False)):
        original = getattr(target, "_candidate_from_item", None)
        if callable(original):

            @wraps(original)
            def metadata_candidate_builder(item: dict, request: Any):
                candidate = original(item, request=request)
                if candidate is None or not isinstance(item, dict):
                    return candidate

                metadata = extract_walmart_product_metadata(
                    item,
                    current_price=getattr(candidate, "api_current_price", None)
                    or getattr(candidate, "current_price", None),
                    reference_price=getattr(candidate, "api_reference_price", None)
                    or getattr(candidate, "typical_price", None),
                    exact_detail=is_exact_detail_request(request),
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

    _install_review_card_bridge()
    _install_public_renderer_bridge()
    return provider


def is_exact_detail_request(request: Any) -> bool:
    """Recognize foreground, queued, recheck, and future exact-detail markers."""

    source_key = str(getattr(request, "source_key", "") or "").strip().lower()
    if source_key.startswith("walmart_exact_detail") or (
        source_key.startswith("walmart") and "exact" in source_key and "detail" in source_key
    ):
        return True

    metadata = getattr(request, "metadata", None)
    values = metadata if isinstance(metadata, dict) else {}
    for key in (
        "exact_detail_price_check",
        "exact_detail",
        "exact_item_detail",
        "detail_recheck",
    ):
        marker = str(values.get(key) or "").strip().lower().replace("-", "_")
        if marker in _EXACT_DETAIL_MARKERS:
            return True
    return False


def walmart_product_metadata_installed(provider: Any) -> bool:
    target = getattr(provider, "inner", provider)
    return bool(getattr(target, _PATCH_FLAG, False))


def _install_review_card_bridge() -> None:
    from sniperplug.services import walmart_review_candidates as review

    if bool(getattr(review, _REVIEW_PATCH_FLAG, False)):
        return
    original = getattr(review, "build_review_card", None)
    if not callable(original):
        return

    @wraps(original)
    def build_review_card_with_metadata(candidate: Any, deal: Any, proof: Any, *args, **kwargs):
        card = original(candidate, deal, proof, *args, **kwargs)
        attrs = dict(getattr(deal, "variant_attributes", None) or {})
        card.candidate = candidate
        card.variant_attributes = attrs
        card.api_current_price = getattr(deal, "api_current_price", None) or getattr(
            deal, "current_price", None
        )
        card.api_reference_price = getattr(deal, "api_reference_price", None) or getattr(
            deal, "typical_price", None
        )
        card.api_reference_path = getattr(deal, "api_reference_path", None) or attrs.get(
            "trustedReferenceSource"
        )
        card.api_discount_percent = getattr(deal, "api_discount_percent", None)
        card.deal_lane = getattr(deal, "deal_lane", None)
        card.variant_label = getattr(deal, "variant_label", None)
        card.pack_size = getattr(deal, "pack_size", None)
        card.color = getattr(deal, "color", None)
        card.platform = getattr(deal, "platform", None)
        card.model = getattr(deal, "model", None)
        card.seller_name = getattr(deal, "seller_name", None) or getattr(
            candidate, "seller_name", None
        )
        card.fulfillment_type = getattr(deal, "fulfillment_type", None) or getattr(
            candidate, "fulfillment_type", None
        )
        card.condition = getattr(deal, "condition", None) or getattr(
            candidate, "condition", None
        )
        return card

    review.build_review_card = build_review_card_with_metadata
    setattr(review, _REVIEW_PATCH_FLAG, True)


def _install_public_renderer_bridge() -> None:
    from sniperplug.services import walmart_card_renderer as renderer

    if bool(getattr(renderer, _RENDERER_PATCH_FLAG, False)):
        return

    original_offer_lines = getattr(renderer, "offer_lines", None)
    original_fulfillment_lines = getattr(renderer, "fulfillment_lines", None)
    if not callable(original_offer_lines) or not callable(original_fulfillment_lines):
        return

    @wraps(original_offer_lines)
    def offer_lines_with_metadata(candidate: Any, deal: Any):
        lines = list(original_offer_lines(candidate, deal))
        attrs = dict(getattr(deal, "variant_attributes", None) or {})
        extra = [
            renderer.maybe_line("Walmart tags", attrs.get("retailerTags")),
            renderer.maybe_line("Purchase context", attrs.get("purchaseContext")),
            renderer.maybe_line("Other condition offers", attrs.get("conditionOptions")),
        ]
        return renderer.compact((*lines, *extra))

    @wraps(original_fulfillment_lines)
    def fulfillment_lines_with_metadata(candidate: Any, deal: Any):
        lines = list(original_fulfillment_lines(candidate, deal))
        attrs = dict(getattr(deal, "variant_attributes", None) or {})
        extra = [
            renderer.maybe_line("Shipping", _method_text(attrs, "shipping")),
            renderer.maybe_line("Pickup", _method_text(attrs, "pickup")),
            renderer.maybe_line("Delivery", _method_text(attrs, "delivery")),
            renderer.maybe_line("Returns", attrs.get("returnPolicy")),
            renderer.maybe_line("API location", attrs.get("fulfillmentLocation")),
        ]
        return renderer.compact((*lines, *extra))

    renderer.offer_lines = offer_lines_with_metadata
    renderer.fulfillment_lines = fulfillment_lines_with_metadata
    setattr(renderer, _RENDERER_PATCH_FLAG, True)


def _method_text(attrs: dict[str, Any], method: str) -> str | None:
    status = str(attrs.get(f"{method}Status") or "").strip()
    text = str(attrs.get(f"{method}Text") or "").strip()
    parts: list[str] = []
    for value in (status, text):
        if value and value not in parts:
            parts.append(value)
    return " — ".join(parts) or None
