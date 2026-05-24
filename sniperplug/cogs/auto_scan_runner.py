from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord.ext import commands, tasks

from sniperplug.cogs.deal_scanner import HUNT_PRESETS, DealCard, provider_health_error_message, run_preset_hunt
from sniperplug.cogs.public_alerts import auto_scan_allowed, record_auto_scan_run
from sniperplug.services.public_deal_posts import get_public_post_config, maybe_post_public_deal_cards


log = logging.getLogger("sniperplug.autoscan")
AUTO_SCAN_INTERVAL_MINUTES = 15
AUTO_SCAN_RETAILER = "walmart"
AUTO_SCAN_SOURCE_LABEL = "autoscan:walmart_discovery"


@dataclass(frozen=True)
class AutoScanGuild:
    guild_id: int
    channel_id: int | None


class AutoScanRunnerCog(commands.Cog):
    """Runs enabled retailer auto-discovery in the background.

    `/retailer_autoscan` owns credit/rate gates. This cog is the missing engine
    that actually wakes up, checks those gates, scans enabled retailers, and
    posts only through the same public-posting duplicate guard used by manual
    scans.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.auto_scan_loop.start()

    async def cog_unload(self) -> None:
        self.auto_scan_loop.cancel()

    @tasks.loop(minutes=AUTO_SCAN_INTERVAL_MINUTES)
    async def auto_scan_loop(self) -> None:
        await self.bot.wait_until_ready()
        guilds = await list_public_alert_guilds(self.bot.db)
        if not guilds:
            return

        health_error = await provider_health_error_message()
        if health_error:
            log.info("Auto-scan skipped: %s", health_error)
            return

        for guild in guilds:
            await self._run_guild_walmart_discovery(guild)

    @auto_scan_loop.before_loop
    async def before_auto_scan_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _run_guild_walmart_discovery(self, guild: AutoScanGuild) -> None:
        scan_key = AUTO_SCAN_SOURCE_LABEL
        allowed, reason, settings = await auto_scan_allowed(
            self.bot.db,
            guild.guild_id,
            AUTO_SCAN_RETAILER,
            scan_key=scan_key,
        )
        if not allowed:
            log.debug("Auto-scan blocked guild=%s retailer=%s reason=%s", guild.guild_id, AUTO_SCAN_RETAILER, reason)
            return

        all_cards: list[DealCard] = []
        warnings: list[str] = []
        products_checked = 0
        searches_checked = 0

        for preset in HUNT_PRESETS.values():
            try:
                cards, pages_checked, checked, preset_warnings, _shown_discount = await run_preset_hunt(
                    preset,
                    requested_by="autoscan",
                )
            except Exception as exc:  # pragma: no cover - runtime/provider guard
                warnings.append(f"{preset.key}: {exc}")
                log.exception("Auto-scan preset failed guild=%s preset=%s", guild.guild_id, preset.key)
                continue
            searches_checked += pages_checked
            products_checked += checked
            warnings.extend(w for w in preset_warnings if w not in warnings)
            all_cards.extend(cards[:3])
            if len(all_cards) >= 12:
                break

        unique_cards = dedupe_cards(all_cards)
        unique_cards.sort(key=lambda card: (card.discount, card.score), reverse=True)
        shown_cards = unique_cards[:5]

        await record_auto_scan_run(self.bot.db, guild.guild_id, AUTO_SCAN_RETAILER, scan_key=scan_key)
        if not shown_cards:
            log.info(
                "Auto-scan completed with no postable cards guild=%s checked=%s searches=%s settings=%s warnings=%s",
                guild.guild_id,
                products_checked,
                searches_checked,
                settings,
                warnings[:3],
            )
            return

        result = await maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=guild.guild_id,
            cards=shown_cards,
            source_label=AUTO_SCAN_SOURCE_LABEL,
            fallback_retailer=AUTO_SCAN_RETAILER,
        )
        log.info(
            "Auto-scan completed guild=%s checked=%s searches=%s cards=%s posted=%s dupes=%s cached=%s errors=%s",
            guild.guild_id,
            products_checked,
            searches_checked,
            len(shown_cards),
            result.posted,
            result.skipped_duplicate,
            result.cached_active,
            result.errors,
        )


def dedupe_cards(cards: list[DealCard]) -> list[DealCard]:
    seen: set[str] = set()
    unique: list[DealCard] = []
    for card in cards:
        key = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or card.url
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    return unique


async def list_public_alert_guilds(db) -> list[AutoScanGuild]:
    """Return guilds that have public posting configured.

    Auto-scan still respects `/retailer_autoscan`; this list only identifies
    where results are allowed to be posted if the retailer gate also passes.
    """
    conn = db.require_conn()
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_public_alert_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            retailers_json TEXT NOT NULL DEFAULT '[]',
            channel_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()
    cursor = await conn.execute(
        "SELECT guild_id, channel_id FROM guild_public_alert_settings WHERE enabled = 1 AND channel_id IS NOT NULL"
    )
    rows = await cursor.fetchall()
    guilds: list[AutoScanGuild] = []
    for row in rows:
        config = await get_public_post_config(db, int(row["guild_id"]))
        if AUTO_SCAN_RETAILER in set(config.get("retailers") or ()):
            guilds.append(AutoScanGuild(guild_id=int(row["guild_id"]), channel_id=int(row["channel_id"])))
    return guilds
