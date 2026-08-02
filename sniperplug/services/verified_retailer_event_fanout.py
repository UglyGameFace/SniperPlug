from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

import discord

from sniperplug.services.autoscan_live_guild_reconciliation import list_live_public_alert_guilds
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent
from sniperplug.services.dm_deal_alerts import (
    clear_dm_delivery_failures,
    dm_alerts_sent_today,
    dm_receipt_exists,
    list_enabled_dm_deal_alert_preferences,
    record_dm_delivery_failure,
    record_dm_receipt,
)
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.hp_deal_cards import build_hp_deal_card
from sniperplug.services.hp_public_posts import maybe_post_hp_deal_cards
from sniperplug.services.verified_retailer_events import (
    claim_verified_retailer_events,
    mark_verified_retailer_event_processed,
    release_verified_retailer_event,
)


_FANOUT_LOCK = asyncio.Lock()
log = logging.getLogger("sniperplug.retailer_event_fanout")


@dataclass(frozen=True)
class VerifiedRetailerFanoutResult:
    events_claimed: int = 0
    events_processed: int = 0
    guilds_checked: int = 0
    public_posts: int = 0
    public_errors: int = 0
    dm_preferences_checked: int = 0
    dm_matches: int = 0
    dm_sent: int = 0
    dm_disabled: int = 0
    dm_errors: int = 0

    def summary_line(self) -> str:
        return (
            "verified retailer fanout: "
            f"claimed **{self.events_claimed}** • processed **{self.events_processed}** • "
            f"guild posts **{self.public_posts}** • DM sent **{self.dm_sent}** • "
            f"errors public/DM **{self.public_errors}/{self.dm_errors}**"
        )


async def fanout_verified_retailer_events(
    bot: Any,
    *,
    event_limit: int = 20,
) -> VerifiedRetailerFanoutResult:
    db = getattr(bot, "db", None)
    if db is None or _FANOUT_LOCK.locked():
        return VerifiedRetailerFanoutResult()

    async with _FANOUT_LOCK:
        events = await claim_verified_retailer_events(db, limit=max(1, int(event_limit)))
        if not events:
            return VerifiedRetailerFanoutResult()

        load_result = await list_live_public_alert_guilds(db, bot)
        guilds = list(load_result.guilds)
        preferences = await list_enabled_dm_deal_alert_preferences(db)
        totals = {
            "events_processed": 0,
            "guilds_checked": 0,
            "public_posts": 0,
            "public_errors": 0,
            "dm_preferences_checked": 0,
            "dm_matches": 0,
            "dm_sent": 0,
            "dm_disabled": 0,
            "dm_errors": 0,
        }

        for event in events:
            errors: list[str] = []
            if event.retailer != "hp":
                await mark_verified_retailer_event_processed(
                    db,
                    event_key=event.event_key,
                    claim_token=event.claim_token,
                    note=f"unsupported verified retailer event discarded safely: {event.retailer}",
                )
                totals["events_processed"] += 1
                continue

            card = build_hp_deal_card(event.candidate, event_key=event.event_key)
            for guild in guilds:
                guild_id = int(guild.guild_id)
                totals["guilds_checked"] += 1
                try:
                    threshold = await get_starting_deal_percent(db, guild_id)
                    result = await maybe_post_hp_deal_cards(
                        bot=bot,
                        guild_id=guild_id,
                        cards=[card],
                        min_public_discount=int(threshold),
                    )
                    totals["public_posts"] += int(result.posted)
                    decided = bool(
                        result.posted
                        or result.skipped_duplicate
                        or result.skipped_not_alertable
                        or result.skipped_disabled
                        or result.skipped_wrong_retailer
                    )
                    if result.errors and not decided:
                        totals["public_errors"] += len(result.errors)
                        errors.extend(result.errors)
                    elif result.errors:
                        log.info(
                            "HP fanout destination completed with notes guild=%s event=%s notes=%s",
                            guild_id,
                            event.event_key,
                            list(result.errors),
                        )
                except Exception as error:  # noqa: BLE001 - one guild cannot block others.
                    totals["public_errors"] += 1
                    errors.append(f"guild {guild_id}: {type(error).__name__}: {error}")
                    log.exception("HP public fanout failed guild=%s event=%s", guild_id, event.event_key)

            for preference in preferences:
                totals["dm_preferences_checked"] += 1
                decision = match_dm_deal(preference, card)
                if not decision.matched:
                    continue
                totals["dm_matches"] += 1
                try:
                    if await dm_receipt_exists(
                        db,
                        user_id=preference.user_id,
                        deal_key=event.event_key,
                    ):
                        continue
                    if await dm_alerts_sent_today(db, preference.user_id) >= preference.max_alerts_per_day:
                        continue
                    user = bot.get_user(preference.user_id)
                    if user is None:
                        user = await bot.fetch_user(preference.user_id)
                    await user.send(embed=_personal_dm_embed(card, decision.reason))
                    await record_dm_receipt(
                        db,
                        user_id=preference.user_id,
                        deal_key=event.event_key,
                    )
                    await clear_dm_delivery_failures(db, user_id=preference.user_id)
                    totals["dm_sent"] += 1
                except discord.Forbidden as error:
                    totals["dm_disabled"] += 1
                    await record_dm_delivery_failure(
                        db,
                        user_id=preference.user_id,
                        error=f"Discord DMs closed: {error}",
                        disable=True,
                    )
                except discord.NotFound as error:
                    totals["dm_disabled"] += 1
                    await record_dm_delivery_failure(
                        db,
                        user_id=preference.user_id,
                        error=f"Discord user unavailable: {error}",
                        disable=True,
                    )
                except Exception as error:  # noqa: BLE001 - one subscriber cannot block others.
                    totals["dm_errors"] += 1
                    errors.append(f"DM {preference.user_id}: {type(error).__name__}: {error}")
                    await record_dm_delivery_failure(
                        db,
                        user_id=preference.user_id,
                        error=f"{type(error).__name__}: {error}",
                        disable=False,
                    )

            if errors:
                await release_verified_retailer_event(
                    db,
                    event_key=event.event_key,
                    claim_token=event.claim_token,
                    error=" | ".join(errors),
                )
            else:
                await mark_verified_retailer_event_processed(
                    db,
                    event_key=event.event_key,
                    claim_token=event.claim_token,
                )
                totals["events_processed"] += 1

        return VerifiedRetailerFanoutResult(events_claimed=len(events), **totals)


def _personal_dm_embed(card: Any, reason: str) -> discord.Embed:
    source = getattr(card, "embed", None)
    embed = discord.Embed.from_dict(source.to_dict()) if source is not None else discord.Embed(title="HP deal")
    value = (
        f"{reason}\n"
        "This came from your opt-in `/dm_deals` filter and was verified against the exact HP.com structured offer. "
        "Prices can change; recheck the product before buying."
    )
    if len(embed.fields) < 25:
        embed.add_field(name="🔔 Your smart alert", value=value[:1024], inline=False)
    return sanitize_embed(embed)
