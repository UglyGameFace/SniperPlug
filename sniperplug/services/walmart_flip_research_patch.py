from __future__ import annotations

import inspect


_PATCHED = False
_ORIGINAL_BUILD_REVIEW_CARD = None


def install_walmart_flip_research_patch() -> None:
    """Add quick comp research links to Walmart review cards with marketplace comps."""
    global _PATCHED, _ORIGINAL_BUILD_REVIEW_CARD
    if _PATCHED:
        return

    from sniperplug.services import walmart_review_candidates

    _ORIGINAL_BUILD_REVIEW_CARD = walmart_review_candidates.build_review_card
    walmart_review_candidates.build_review_card = _build_review_card_with_flip_research
    _PATCHED = True


def _build_review_card_with_flip_research(
    candidate,
    deal,
    proof,
    *,
    context_price,
    context_discount,
    ignored_context_price,
    coupon,
    cash,
    direct_match_score: float = 0.0,
):
    from sniperplug.services.walmart_marketplace_comp_guard import comp_search_links

    kwargs = {
        "context_price": context_price,
        "context_discount": context_discount,
        "ignored_context_price": ignored_context_price,
        "coupon": coupon,
        "cash": cash,
    }
    if "direct_match_score" in inspect.signature(_ORIGINAL_BUILD_REVIEW_CARD).parameters:
        kwargs["direct_match_score"] = direct_match_score

    card = _ORIGINAL_BUILD_REVIEW_CARD(candidate, deal, proof, **kwargs)
    attrs = deal.variant_attributes or {}
    if not attrs.get("marketplaceCompPrice"):
        return card

    links = comp_search_links(title=deal.title, sku=deal.sku, upc=deal.upc)
    if links:
        card.embed.add_field(
            name="🔎 Flip comp checks",
            value=" • ".join(links) + "\nVerify sold comps, fees, shipping, carrier locks, and sell-through before buying.",
            inline=False,
        )
    return card
