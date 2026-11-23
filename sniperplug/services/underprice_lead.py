from __future__ import annotations

"""First-party extreme-underprice leads: the mirror of the listing-error lane.

The listing-error detector (``listing_contradiction``) catches a listing
that disagrees with *itself*. This lane catches the opposite shape: the
listing is internally consistent, its exact-verified price is simply far
below the price Walmart itself lists for the same item — the classic
price-error silhouette (a $69.99 digital game sold by Walmart.com at
$2.97, with no was-price anywhere, so no verified-markdown lane can ever
fire and the observed-price-drop lane has nothing stable to compare
against on a fresh database).

Why this is a DM-only speculative lead and never anything stronger:

* The reference is Walmart's own list/MSRP price. That is source data,
  not a verified was-price: MSRPs can be inflated, so no discount
  percentage is ever claimed as fact and the card states both numbers
  with the list price labelled unverified.
* The public extreme-price lane deliberately excludes software/digital
  goods and sub-$100 list prices. Those exclusions stay exactly as they
  are for public posts. This lane exists only inside personal DMs for
  subscribers who opted into the price-mistake route, where the lead is
  ranked by its implied gap against the list price under the
  subscriber's own floors (the same treatment evidence-backed
  listing-error leads get in ``dm_deal_matching``).

Fail-closed requirements, all of them:

* exact-detail price proof on the candidate (this lane only ever sees
  exact-verified offers);
* the exact price is at most 20% of a Walmart-returned list price of at
  least $50 (the shared price-error ratio — which also implies a gap of
  at least $40);
* the seller is Walmart first-party or a platform owner's own store
  (Microsoft, Sony, Nintendo, Apple, Samsung, LEGO) — an unknown
  marketplace seller at a fake-low price is a scam shape, not an error
  shape, and its list price proves nothing about value;
* not an accessory/part, not a cheap consumable, and not in a department
  where list prices are routinely fictional (apparel, jewelry, shoes,
  beauty, decor — the shared MSRP-inflation screen). Software and
  digital goods are *allowed* here precisely because the delivery is a
  labelled private lead, not a public claim;
* the list price is not Walmart's $99.99 fallback value (returned for
  items with no real list price at all — e.g. Pen+Gear pens at their
  everyday $2.94, DM'd 2026-11-23 as a "97% gap"), and the item is not
  low-ticket office/school stationery, whose list prices are fictional
  relative to a $1–$15 everyday price;
* the item is not a gift card or prepaid credit (a gift card's "price"
  is a buyer-chosen denomination, so a large returned list is the
  denomination menu, not value evidence), not food or grocery, and not
  a cleaning or household chemical — those categories carry the same
  fictional-list pattern (taco seasoning at $0.48 against a $1,149.99
  list, Clorox bleach at $6.26 against $113.99, both in the saved
  2026-11-23 fire set) and stay reachable only through the
  verified-markdown and observed-price-drop lanes;
* the item is not an electronics accessory and not a health or
  personal-care consumable — the durable-type lists treat headsets
  as primary products, but in this list-price-only lane they are
  screened like the accessories they are (a Logitech H390 at its
  everyday $29.56 against a $240 list), and health consumables carry
  the same fictional lists as food and cleaning chemicals (GenTeal
  eye ointment at $11.26 against a $1,149.99 list; both in the saved
  2026-11-23 fire set). Consoles, phones, laptops and TVs are
  primary hardware and are not touched by the accessory screen;
* the offer is cart-buyable, in stock, and not a member-only or
  checkout-only price.
"""

from typing import Any

from sniperplug.services.stock_status import is_unavailable_stock
from sniperplug.services.walmart_extreme_price_signals import (
    CHEAP_CONSUMABLE,
    CLEANING_CHEMICAL_GOODS,
    FOOD_GROCERY_CATEGORY,
    FOOD_GROCERY_GOODS,
    GIFT_CARD_GOODS,
    HEALTH_CONSUMABLE_CATEGORY,
    HEALTH_CONSUMABLE_GOODS,
    LOW_TICKET_SUPPLIES,
    MSRP_INFLATED_GOODS,
    is_accessory_title,
    is_electronics_accessory_category,
    is_electronics_accessory_title,
    is_price_error_ratio,
    is_walmart_first_party,
    walmart_listed_reference,
)

# Official manufacturer / platform-owner storefronts on Walmart. A
# price error is plausible from these sellers; a too-cheap listing from
# an unrecognized marketplace seller is not evidence of anything.
PLATFORM_OWNER_SELLERS = {
    "microsoft",
    "sony interactive entertainment",
    "nintendo",
    "apple",
    "samsung",
    "lego",
}


def trusted_lead_seller(candidate: Any) -> bool:
    """True for Walmart itself or a platform owner's official store."""

    if is_walmart_first_party(candidate):
        return True
    attrs = getattr(candidate, "variant_attributes", None) or {}
    seller = " ".join(
        str(
            getattr(candidate, "seller_name", None) or attrs.get("seller") or ""
        ).lower().split()
    )
    return seller in PLATFORM_OWNER_SELLERS


def _positive(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return number if number > 0 else None


def underprice_lead_reference(candidate: Any) -> float | None:
    """Return Walmart's list price when this exact-verified offer is an
    extreme first-party underprice lead, else None.

    The returned price is the specific list reference that satisfied the
    evidence check — the same value callers use to measure the implied
    gap. It is a list price, never a verified was-price.
    """

    if candidate is None:
        return None
    attrs = getattr(candidate, "variant_attributes", None) or {}
    if str(attrs.get("exactDetailPriceProof") or "").strip().lower() != "yes":
        return None
    current = _positive(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    listed = walmart_listed_reference(candidate)
    if current is None or listed is None:
        return None
    if not is_price_error_ratio(current, listed):
        return None
    if not trusted_lead_seller(candidate):
        return None
    title = " ".join(
        str(getattr(candidate, key, "") or "")
        for key in ("title", "parent_title")
    ).strip()
    if not title or is_accessory_title(title):
        return None
    if CHEAP_CONSUMABLE.search(title):
        return None
    category = str(attrs.get("categoryPath") or attrs.get("category") or "")
    if MSRP_INFLATED_GOODS.search(title) or MSRP_INFLATED_GOODS.search(category):
        return None
    if LOW_TICKET_SUPPLIES.search(title) or LOW_TICKET_SUPPLIES.search(category):
        return None
    if GIFT_CARD_GOODS.search(title) or GIFT_CARD_GOODS.search(category):
        return None
    if FOOD_GROCERY_GOODS.search(title) or FOOD_GROCERY_CATEGORY.search(category):
        return None
    if CLEANING_CHEMICAL_GOODS.search(title) or CLEANING_CHEMICAL_GOODS.search(
        category
    ):
        return None
    if is_electronics_accessory_title(title) or is_electronics_accessory_category(
        category
    ):
        return None
    if HEALTH_CONSUMABLE_GOODS.search(title) or HEALTH_CONSUMABLE_CATEGORY.search(
        category
    ):
        return None
    if getattr(candidate, "can_add_to_cart", None) is not True:
        return None
    if is_unavailable_stock(getattr(candidate, "stock_status", None)):
        return None
    if getattr(candidate, "is_member_only", False) or getattr(
        candidate, "is_checkout_price", False
    ):
        return None
    online = str(attrs.get("availableOnline") or "").strip().lower()
    if online in {"no", "false", "0", "off"}:
        return None
    return listed


def build_underprice_lead_card(candidate: Any) -> Any | None:
    """Build the DM-only underprice lead card, or None.

    Reuses the scout card builder exactly as the listing-error lane
    does, then replaces its framing: this is a speculative lead about an
    exact-verified price versus Walmart's own list price — NOT a
    verified markdown, and no discount percentage is claimed. The card
    is deliverable only when ``underprice_lead_reference`` holds.
    """

    listed = underprice_lead_reference(candidate)
    if listed is None:
        return None
    from sniperplug.services.low_price_scout import ScoutLead, build_scout_card

    current = _positive(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    seller = str(getattr(candidate, "seller_name", None) or "Walmart").strip()
    evidence = (
        f"exact-verified price ${current:,.2f} is at or below 20% of "
        f"Walmart's own list price ${listed:,.2f} for the same item, "
        f"sold by {seller}"
    )
    lead = ScoutLead(
        candidate=candidate,
        score=70,
        reasons=("first-party underprice lead", evidence),
    )
    card = build_scout_card(lead)
    card.url = getattr(candidate, "direct_product_url", None) or candidate.product_url
    card.embed.url = card.url
    card.embed.title = "Underprice lead: " + str(candidate.title or "")[:155]
    # The scout card derives a % from any raw reference it finds; an
    # underprice lead must never present that as a verified discount.
    # Strip every scout price field and replace it with the lead facts.
    card.embed.clear_fields()
    facts: list[str] = [
        f"Exact-verified price: **${current:,.2f}**.",
        (
            f"Walmart list price: **${listed:,.2f}** — Walmart's own "
            "list/MSRP price for this item. It is unverified: it is NOT "
            "a was-price and no discount percentage is claimed."
        ),
        f"Value evidence: {evidence}.",
        f"Seller: {str(getattr(candidate, 'seller_name', None) or 'Walmart').strip()} "
        "(first-party / platform-owner store).",
        (
            "This looks like a possible price error, but a low price "
            "against a list price can also be a deliberate deep discount. "
            "Recheck the exact item, condition and checkout price before buying."
        ),
    ]
    card.embed.add_field(
        name="Extreme underprice lead, NOT a verified markdown",
        value=" ".join(facts),
        inline=False,
    )
    card.embed.set_footer(
        text="Speculative underprice lead; list price unverified; verify before buying"
    )
    card.underprice_lead_candidate = candidate
    card.underprice_lead = True
    card.should_alert = True
    return card
