"""Deal cards for Home Depot penny-tracker events.

Modeled on :mod:`sniperplug.services.hp_deal_cards`: the tracker publishes
a durable candidate snapshot, the retailer fanout builds the card from it,
and the public-post gate re-verifies the card's structured proof fields
before anything reaches a deals channel.

Copy rules (Felix's standing bar): the word "guaranteed" never appears.
The penny card states the proven fact — the system price was observed at
exactly $0.01 — and says plainly that finding the item on the shelf is
the shopper's in-store check. The final-stage card uses the sourced
cadence copy: final markdown stage, the ones that drop to a penny next.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.hd_penny_stages import PennyStage, decode_price_stage, price_to_cents


def build_hd_penny_card(candidate: SourceCandidate, *, event_key: str = "") -> DealCard:
    decision = evaluate_candidate(candidate)
    deal = decision.deal
    current = _float(candidate.api_current_price or candidate.current_price)
    reference = _float(candidate.api_reference_price or candidate.typical_price)
    discount = _float(candidate.api_discount_percent)
    if discount is None and current is not None and reference is not None and reference > current:
        discount = round((reference - current) / reference * 100.0, 2)
    discount = discount or 0.0
    savings = round(reference - current, 2) if current is not None and reference is not None else None

    attrs = dict(candidate.variant_attributes or {})
    stage = _stage_from_candidate(candidate, attrs)
    is_penny = stage == PennyStage.PENNY
    store_label = str(attrs.get("hdPennyStore") or "online")
    where = (
        f"store `{store_label}`"
        if store_label != "online"
        else "the online listing (store prices run on their own clock)"
    )

    if is_penny:
        embed = discord.Embed(
            title=f"🚨 PENNY PRICE — {_trim(candidate.title, 90)}",
            url=candidate.direct_product_url or candidate.product_url,
            description=(
                "Home Depot's system price for this item was just observed at "
                "**$0.01** — the penny price an item drops to when its markdown "
                "run ends and it is supposed to be pulled from the shelf."
            ),
            color=discord.Color.red(),
        )
    else:
        embed = discord.Embed(
            title=f"🏷️ FINAL MARKDOWN — {_trim(candidate.title, 90)}",
            url=candidate.direct_product_url or candidate.product_url,
            description=(
                "This item just entered the **final markdown stage** — these are "
                "the ones that drop to a penny next. Worth looking for in the "
                "store now, before the drop or the pull."
            ),
            color=discord.Color.orange(),
        )
    if candidate.image_url:
        embed.set_thumbnail(url=candidate.image_url)

    embed.add_field(
        name="💵 Price now",
        value=f"**${current:,.2f}**" if current is not None else "Not returned",
        inline=True,
    )
    if reference is not None:
        embed.add_field(
            name="🏷️ Was",
            value=f"${reference:,.2f}",
            inline=True,
        )
        embed.add_field(
            name="📉 Markdown",
            value=(
                f"**{discount:.0f}% off** • save **${savings:,.2f}**"
                if savings is not None
                else f"**{discount:.0f}% off**"
            ),
            inline=True,
        )
    embed.add_field(
        name="🧭 Stage",
        value=_stage_block(candidate, attrs, stage),
        inline=False,
    )
    embed.add_field(
        name="📍 Where",
        value=f"Price observed for {where}. Clearance runs per store, so a nearby store can differ.",
        inline=False,
    )
    if is_penny:
        embed.add_field(
            name="🛒 What to do",
            value=(
                "Check the shelf at your store and scan the item: a penny price "
                "is only proven when it scans at $0.01 in the store. If it is "
                "still on the shelf it should ring up at a penny — stores are "
                "supposed to pull these, so do not wait."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="🛒 What to do",
            value=(
                "Look for it on the clearance endcaps at your store and check "
                "the tag price. Keep an eye on it here — if it drops to $0.01, "
                "the penny alert fires."
            ),
            inline=False,
        )
    embed.add_field(
        name="✅ What this proves",
        value=(
            "The price above was observed in Home Depot's own product data "
            "by the penny tracker. It does **not** prove the item is still on "
            "the shelf — that part is your in-store check."
        ),
        inline=False,
    )
    embed.set_footer(
        text="SniperPlug Home Depot penny tracker • Prices can change or be pulled at any time."
    )

    card = DealCard(
        embed=embed,
        url=deal.product_url,
        label=_button_label(deal.title),
        score=max(100, int(decision.anomaly.score)),
        discount=discount,
    )
    card.retailer = "Home Depot"
    card.current_price = current
    card.typical_price = reference
    card.should_alert = True
    card.deal_lane = candidate.deal_lane or "hd_penny_final_stage"
    card.api_current_price = current
    card.api_reference_price = reference
    card.api_discount_percent = discount
    card.api_condition = candidate.api_condition or candidate.condition
    card.api_condition_path = candidate.api_condition_path
    card.api_reference_path = candidate.api_reference_path
    card.api_price_path = candidate.api_price_path
    card.seller_name = candidate.seller_name or "Home Depot"
    card.fulfillment_type = candidate.fulfillment_type
    card.direct_product_url = candidate.direct_product_url or candidate.product_url
    card.variant_attributes = dict(candidate.variant_attributes or {})
    card.sku = candidate.sku
    card.selected_offer_id = candidate.selected_offer_id
    card.candidate = candidate
    card.deal = deal
    card.hd_penny_stage = stage.value
    if event_key:
        card.public_post_key = event_key
    return card


def _stage_from_candidate(candidate: SourceCandidate, attrs: dict[str, str]) -> PennyStage:
    raw = str(attrs.get("hdPennyStage") or "").strip()
    if raw:
        try:
            return PennyStage(raw)
        except ValueError:
            pass
    reading = decode_price_stage(
        price_to_cents(candidate.api_current_price or candidate.current_price),
        clearance_context=True,
    )
    return reading.stage


def _stage_block(candidate: SourceCandidate, attrs: dict[str, str], stage: PennyStage) -> str:
    current = candidate.api_current_price or candidate.current_price
    cents = price_to_cents(current)
    ending = f"{cents % 100:02d}" if cents is not None else "??"
    if stage == PennyStage.PENNY:
        lines = ["**Penny** — the markdown run is over; the system price is $0.01."]
    elif ending == "02":
        lines = [
            "Price ends in **.02** — the last step on the faster markdown "
            "ladder, right before the penny."
        ]
    else:
        lines = [
            "Price ends in **.03** — the final markdown ending. Items sitting "
            "at this stage drop to $0.01 next, or get pulled."
        ]
    entered = _parse_iso(attrs.get("hdPennyStageEnteredAt"))
    observed = _parse_iso(attrs.get("hdPennyObservedAt"))
    if entered is not None and observed is not None:
        days = max(0.0, (observed - entered).total_seconds() / 86400.0)
        lines.append(f"In this stage for **{days:.0f} day(s)** so far.")
    return "\n".join(lines)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _trim(value: str, limit: int) -> str:
    cleaned = " ".join(str(value or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _button_label(value: str) -> str:
    text = " ".join(str(value or "Home Depot penny watch").split())
    return text[:77] + "..." if len(text) > 80 else text
