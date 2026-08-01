from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.public_deal_quality import LANE_PRICE_MEMORY_DROP
from sniperplug.services.public_posting import normalize_retailer_key
from sniperplug.services.safe_links import product_link_choices
from sniperplug.services.walmart_global_offer_memory import (
    ExactOfferIdentity,
    GlobalOfferObservation,
    ensure_global_offer_memory_table,
    exact_offer_identity,
    maybe_prune_global_offer_memory,
    observe_exact_offer,
)


MIN_MEMORY_DROP_DOLLARS = 5.00
DEFAULT_OBSERVED_MEMORY_MAX_WRITES = 300


@dataclass(frozen=True)
class ObservedPriceMemoryDecision:
    candidate: SourceCandidate
    identity_key: str
    status: str
    previous_price: float | None
    current_price: float | None
    lowest_seen_price: float | None
    stable_reference_price: float | None = None
    stable_seen_count: int = 0
    candidate_seen_count: int = 0
    drop_percent: float = 0.0
    drop_dollars: float = 0.0
    reason: str = ""

    @property
    def should_public_post(self) -> bool:
        return (
            self.status in {"lower_price", "new_low"}
            and self.stable_reference_price is not None
            and self.drop_percent > 0
            and self.drop_dollars > 0
        )


@dataclass(frozen=True)
class ObservedPriceMemorySelection:
    cards: list[DealCard]
    decisions: list[ObservedPriceMemoryDecision]
    skipped_due_to_load_cap: int = 0

    def summary_line(self) -> str:
        counts: dict[str, int] = {}
        for decision in self.decisions:
            counts[decision.status] = counts.get(decision.status, 0) + 1
        if not counts:
            return "global exact-offer price memory: no products checked"
        order = (
            "learning",
            "new_low",
            "lower_price",
            "same_or_higher",
            "unverified_identity",
            "identity_collision",
            "missing_price",
            "not_buyable",
        )
        parts = [f"{label}: **{counts[label]}**" for label in order if counts.get(label)]
        if self.skipped_due_to_load_cap:
            parts.append(f"load-capped: **{self.skipped_due_to_load_cap}**")
        parts.append(f"public price-drop cards: **{len(self.cards)}**")
        return "global exact-offer price memory: " + " • ".join(parts)


async def select_observed_price_drop_cards(
    db: Any,
    *,
    guild_id: int | None,
    candidates: list[SourceCandidate],
    min_discount: int = 50,
    limit: int = 5,
    max_observations: int = DEFAULT_OBSERVED_MEMORY_MAX_WRITES,
) -> ObservedPriceMemorySelection:
    """Record compact global exact-offer prices and return public-safe drops.

    The table is shared across guilds and stores no title, image, URL, review
    text, or raw API payload. Public proof fails closed unless Walmart's exact
    detail endpoint confirmed the item and the full offer fingerprint matches.
    """

    if db is None or guild_id is None or not candidates:
        return ObservedPriceMemorySelection(cards=[], decisions=[])

    await ensure_global_offer_memory_table(db)
    conn = db.require_conn()
    await maybe_prune_global_offer_memory(conn)

    decisions: list[ObservedPriceMemoryDecision] = []
    cards: list[DealCard] = []
    bounded_candidates = prioritized_observation_candidates(candidates, limit=max_observations)
    skipped_due_to_load_cap = max(0, len(candidates) - len(bounded_candidates))

    for candidate in bounded_candidates:
        retailer = normalize_retailer_key(candidate.retailer) or "walmart"
        if retailer != "walmart":
            continue

        identity = exact_offer_identity(candidate)
        current_price = float_or_none(candidate.api_current_price or candidate.current_price)
        if identity is None:
            decisions.append(
                ObservedPriceMemoryDecision(
                    candidate=candidate,
                    identity_key="",
                    status="unverified_identity",
                    previous_price=None,
                    current_price=current_price,
                    lowest_seen_price=None,
                    reason="exact Walmart detail item/offer/seller/variant fingerprint was not complete",
                )
            )
            continue

        if current_price is None or current_price <= 0:
            decisions.append(
                ObservedPriceMemoryDecision(
                    candidate=candidate,
                    identity_key=identity.identity_key,
                    status="missing_price",
                    previous_price=None,
                    current_price=current_price,
                    lowest_seen_price=None,
                    reason="exact Walmart detail current price missing",
                )
            )
            continue

        if not is_candidate_buyable(candidate):
            decisions.append(
                ObservedPriceMemoryDecision(
                    candidate=candidate,
                    identity_key=identity.identity_key,
                    status="not_buyable",
                    previous_price=None,
                    current_price=current_price,
                    lowest_seen_price=None,
                    reason="exact offer is not currently buyable enough to train or post",
                )
            )
            continue

        observation = await observe_exact_offer(
            conn,
            candidate=candidate,
            identity=identity,
            min_discount=min_discount,
            min_drop_dollars=MIN_MEMORY_DROP_DOLLARS,
        )
        decision = _decision_from_observation(candidate, observation)
        decisions.append(decision)
        if decision.should_public_post and len(cards) < max(1, int(limit)):
            cards.append(
                build_observed_price_drop_card(
                    candidate,
                    identity,
                    decision,
                    min_discount=min_discount,
                )
            )

    await conn.commit()
    cards.sort(
        key=lambda card: float(getattr(card, "api_discount_percent", 0) or 0),
        reverse=True,
    )
    return ObservedPriceMemorySelection(
        cards=cards[:limit],
        decisions=decisions,
        skipped_due_to_load_cap=skipped_due_to_load_cap,
    )


def prioritized_observation_candidates(
    candidates: list[SourceCandidate],
    *,
    limit: int,
) -> list[SourceCandidate]:
    limit = max(1, int(limit or DEFAULT_OBSERVED_MEMORY_MAX_WRITES))
    deduped: list[SourceCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        identity = exact_offer_identity(candidate)
        fallback = (
            str(getattr(candidate, "product_id", None) or "")
            or str(getattr(candidate, "sku", None) or "")
            or canonical_url_key(candidate.product_url)
            or str(id(candidate))
        )
        key = identity.identity_key if identity is not None else f"unverified:{fallback}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    def rank(candidate: SourceCandidate) -> tuple[int, float, str]:
        identity = exact_offer_identity(candidate)
        current = float_or_none(candidate.api_current_price or candidate.current_price)
        buyable = is_candidate_buyable(candidate)
        if identity is not None and current is not None and buyable:
            bucket = 0
        elif identity is not None and current is not None:
            bucket = 1
        elif identity is not None:
            bucket = 2
        else:
            bucket = 3
        return bucket, float(current or 0), str(candidate.title or "")

    return sorted(deduped, key=rank)[:limit]


def build_observed_price_drop_card(
    candidate: SourceCandidate,
    identity: ExactOfferIdentity,
    decision: ObservedPriceMemoryDecision,
    *,
    min_discount: int,
) -> DealCard:
    reference = decision.stable_reference_price
    embed = discord.Embed(
        title=f"📉 Walmart exact-offer price drop • {_short(candidate.title, 76)}",
        url=candidate.direct_product_url or candidate.product_url,
        description=(
            "SniperPlug compared the same exact Walmart item, selected offer, seller, "
            "variant, condition, and fulfillment against a repeatedly confirmed price. "
            "No MSRP, marketplace comparison, search-result was price, or query wording is used."
        ),
        color=discord.Color.green(),
    )
    if candidate.image_url:
        embed.set_thumbnail(url=candidate.image_url)
    embed.add_field(
        name="✅ Stable exact-offer proof",
        value=(
            f"Stable observed price: **{money(reference)}**\n"
            f"Current exact-detail price: **{money(decision.current_price)}**\n"
            f"Observed drop: **{decision.drop_percent:.0f}%** / **{money(decision.drop_dollars)}**\n"
            f"Stable confirmations: **{decision.stable_seen_count}+**\n"
            f"Server threshold: **{int(min_discount)}%+**\n"
            f"Item: `{identity.item_id}` • Offer fingerprint: `{identity.identity_key[-16:]}`"
        ),
        inline=False,
    )
    choices = product_link_choices(
        retailer=candidate.retailer,
        product_url=candidate.product_url,
        title=candidate.title,
        product_id=candidate.product_id,
        sku=candidate.sku,
        upc=candidate.upc,
    )
    attrs = dict(candidate.variant_attributes or {})
    attrs.update(
        {
            "priceMemoryIdentity": identity.identity_key,
            "priceMemoryItemId": identity.item_id,
            "priceMemoryOfferId": identity.offer_id,
            "priceMemorySellerKey": identity.seller_key,
            "priceMemoryVariantKey": identity.variant_key,
            "priceMemoryConditionKey": identity.condition_key,
            "priceMemoryFulfillmentKey": identity.fulfillment_key,
            "priceMemoryStableConfirmations": str(decision.stable_seen_count),
            "priceMemoryReason": decision.reason,
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": f"{float(reference or 0):.2f}",
            "trustedReferenceSource": "sniperplug.global_exact_offer_memory.stable_price",
        }
    )
    card = DealCard(
        embed=embed,
        url=candidate.product_url,
        label=candidate.title,
        score=100,
        discount=decision.drop_percent,
        link_choices=choices,
        deal_lane=LANE_PRICE_MEMORY_DROP,
        api_current_price=decision.current_price,
        api_reference_price=reference,
        api_discount_percent=decision.drop_percent,
        api_reference_path="sniperplug.global_exact_offer_memory.stable_price",
        api_price_path="walmart.exact_detail.current_price",
        seller_name=candidate.seller_name,
        fulfillment_type=candidate.fulfillment_type,
        direct_product_url=candidate.direct_product_url or candidate.product_url,
        variant_attributes=attrs,
    )
    card.retailer = candidate.retailer
    card.current_price = decision.current_price
    card.typical_price = reference
    card.should_alert = True
    card.sku = candidate.sku
    card.upc = candidate.upc
    card.selected_offer_id = candidate.selected_offer_id
    current_cents = int(round(float(decision.current_price or 0) * 100))
    card.public_post_key = (
        f"global_exact_offer_drop:{identity.identity_key}:{current_cents}"
    )
    return card


def candidate_identity(candidate: SourceCandidate) -> str:
    identity = exact_offer_identity(candidate)
    return identity.identity_key if identity is not None else ""


def is_candidate_buyable(candidate: SourceCandidate) -> bool:
    if (
        candidate.option_mismatch_warning
        or candidate.is_member_only
        or candidate.is_checkout_price
        or candidate.is_business_offer
    ):
        return False
    stock = " ".join(str(candidate.stock_status or "").lower().split())
    if any(
        term in stock
        for term in ("out of stock", "unavailable", "not available", "sold out")
    ):
        return False
    if candidate.can_add_to_cart is False:
        return False
    return True


def canonical_url_key(url: str | None) -> str:
    text = str(url or "").strip().split("?", 1)[0].rstrip("/")
    if "/ip/" in text:
        return text.rsplit("/ip/", 1)[-1].strip("/")
    return text


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):,.2f}"


def _decision_from_observation(
    candidate: SourceCandidate,
    observation: GlobalOfferObservation,
) -> ObservedPriceMemoryDecision:
    return ObservedPriceMemoryDecision(
        candidate=candidate,
        identity_key=observation.identity.identity_key,
        status=observation.status,
        previous_price=observation.previous_price,
        current_price=observation.current_price,
        lowest_seen_price=observation.lowest_seen_price,
        stable_reference_price=observation.stable_reference_price,
        stable_seen_count=observation.stable_seen_count,
        candidate_seen_count=observation.candidate_seen_count,
        drop_percent=observation.drop_percent,
        drop_dollars=observation.drop_dollars,
        reason=observation.reason,
    )


def _short(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
