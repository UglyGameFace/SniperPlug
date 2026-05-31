from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands, tasks

from sniperplug.cogs.deal_scanner import HUNT_PRESETS, DealCard, provider_health_error_message, run_preset_hunt
from sniperplug.cogs.public_alerts import auto_scan_allowed, record_auto_scan_run
from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.public_deal_posts import PublicPostResult, get_public_post_config, maybe_post_public_deal_cards
from sniperplug.services.public_result_explainer import explain_public_post_result


log = logging.getLogger("sniperplug.autoscan")
AUTO_SCAN_INTERVAL_MINUTES = 15
AUTO_SCAN_RETAILER = "walmart"
AUTO_SCAN_SOURCE_LABEL = "autoscan:walmart_discovery"
AUTO_SCAN_PUBLIC_LIMIT = 5


@dataclass(frozen=True)
class AutoScanGuild:
    guild_id: int
    channel_id: int | None


@dataclass(frozen=True)
class AutoScanReport:
    guild_id: int
    allowed: bool
    reason: str = ""
    settings: dict | None = None
    products_checked: int = 0
    searches_checked: int = 0
    total_cards: int = 0
    fresh_cards: int = 0
    cards_attempted_for_public: int = 0
    used_repeat_fallback: bool = False
    repeat_summary: str = ""
    public_result: PublicPostResult = PublicPostResult()
    warnings: tuple[str, ...] = ()

    def log_fields(self) -> dict:
        return {
            "guild": self.guild_id,
            "allowed": self.allowed,
            "reason": compact_log_text(self.reason),
            "checked": self.products_checked,
            "searches": self.searches_checked,
            "total_cards": self.total_cards,
            "fresh_cards": self.fresh_cards,
            "public_attempt": self.cards_attempted_for_public,
            "repeat_fallback": self.used_repeat_fallback,
            "repeat_summary": compact_log_text(self.repeat_summary),
            "posted": self.public_result.posted,
            "dupes": self.public_result.skipped_duplicate,
            "not_alertable": self.public_result.skipped_not_alertable,
            "disabled": self.public_result.skipped_disabled,
            "errors": self.public_result.errors,
            "settings": self.settings or {},
            "warnings": self.warnings[:3],
        }

    def discord_summary(self) -> str:
        if not self.allowed:
            return f"Auto-scan did not run: {self.reason}"
        result = self.public_result
        return (
            f"Checked **{self.products_checked}** products across **{self.searches_checked}** searches.\n"
            f"Candidates: **{self.total_cards}** • Active-cache fresh: **{self.fresh_cards}** • Sent to public guard: **{self.cards_attempted_for_public}**\n"
            f"Posted: **{result.posted}** • Duplicates blocked: **{result.skipped_duplicate}** • Not alertable: **{result.skipped_not_alertable}** • Disabled: **{result.skipped_disabled}**\n"
            f"Repeat fallback used: **{'yes' if self.used_repeat_fallback else 'no'}**\n"
            f"Fresh filter: {self.repeat_summary or 'n/a'}"
        )


class AutoScanRunnerCog(commands.Cog):
    """Runs enabled retailer auto-discovery in the background."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.auto_scan_loop.start()

    async def cog_unload(self) -> None:
        self.auto_scan_loop.cancel()

    @app_commands.command(name="autoscan_now", description="Run the Walmart auto-scan now and show why it did or did not post.")
    @app_commands.describe(force="Bypass interval/daily gate for this manual test. Public alerts still must be configured.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autoscan_now(self, interaction: discord.Interaction, force: bool = True) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which auto-scan settings to test.", ephemeral=True)
            return
        config = await get_public_post_config(self.bot.db, interaction.guild_id)
        if not config.get("enabled") or not config.get("channel_id"):
            await interaction.followup.send("Public alerts are not configured yet. Run `/public_alerts enabled:true retailers:walmart channel:#your-channel` first.", ephemeral=True)
            return
        if AUTO_SCAN_RETAILER not in set(config.get("retailers") or ()):  # public config controls where auto-scan may post
            await interaction.followup.send("Public alerts are enabled, but Walmart is not in the public retailer list. Add `walmart` with `/public_alerts`.", ephemeral=True)
            return
        report = await self._run_guild_walmart_discovery(AutoScanGuild(interaction.guild_id, config.get("channel_id")), force=force)
        embed = discord.Embed(title="🧭 Auto-scan test result", description=report.discord_summary(), color=discord.Color.green() if report.public_result.posted else discord.Color.orange())
        if report.warnings:
            embed.add_field(name="Warnings", value="\n".join(f"• {warning}" for warning in report.warnings[:5]), inline=False)
        if report.public_result.errors:
            embed.add_field(name="Errors", value="\n".join(f"• {error}" for error in report.public_result.errors[:5]), inline=False)
        embed.set_footer(text="This uses the same auto-scan path as the background loop.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @autoscan_now.error
    async def autoscan_now_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to run an auto-scan test." if isinstance(error, app_commands.MissingPermissions) else f"Auto-scan test hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

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

    async def _run_guild_walmart_discovery(self, guild: AutoScanGuild, *, force: bool = False) -> AutoScanReport:
        scan_key = AUTO_SCAN_SOURCE_LABEL
        settings: dict = {}
        if not force:
            allowed, reason, settings = await auto_scan_allowed(
                self.bot.db,
                guild.guild_id,
                AUTO_SCAN_RETAILER,
                scan_key=scan_key,
            )
            if not allowed:
                log.debug("Auto-scan blocked guild=%s retailer=%s reason=%s", guild.guild_id, AUTO_SCAN_RETAILER, reason)
                return AutoScanReport(guild_id=guild.guild_id, allowed=False, reason=reason, settings=settings)
        else:
            settings = {"forced": True, "retailer": AUTO_SCAN_RETAILER}

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

        unique_cards = dedupe_cards(all_cards)
        unique_cards.sort(key=lambda card: (card.discount, card.score), reverse=True)
        fresh_selection = await select_fresh_deal_cards(
            self.bot.db,
            guild_id=guild.guild_id,
            cards=unique_cards,
            fallback_retailer=AUTO_SCAN_RETAILER,
            limit=AUTO_SCAN_PUBLIC_LIMIT,
        )

        # Important: active deal cache is not the same as public-post history.
        # Manual scans can fill active cache before a public alert ever posts.
        # If active-cache freshness says "nothing new", still send the best
        # candidates through the public posting guard so alert_dedupe decides.
        # This fixes waking up to nothing because manual/private scans marked
        # everything as repeated before the background poster could alert it.
        shown_cards = fresh_selection.fresh
        used_repeat_fallback = False
        if not shown_cards and unique_cards:
            shown_cards = unique_cards[:AUTO_SCAN_PUBLIC_LIMIT]
            used_repeat_fallback = True

        if not force:
            await record_auto_scan_run(self.bot.db, guild.guild_id, AUTO_SCAN_RETAILER, scan_key=scan_key)

        if not shown_cards:
            report = AutoScanReport(
                guild_id=guild.guild_id,
                allowed=True,
                reason="Auto-scan completed with no candidate cards.",
                settings=settings,
                products_checked=products_checked,
                searches_checked=searches_checked,
                total_cards=len(unique_cards),
                fresh_cards=0,
                cards_attempted_for_public=0,
                repeat_summary=fresh_selection.summary_line(),
                warnings=tuple(warnings),
            )
            log.info("Auto-scan completed with no cards %s", report.log_fields())
            return report

        result = await maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=guild.guild_id,
            cards=shown_cards,
            source_label=AUTO_SCAN_SOURCE_LABEL,
            fallback_retailer=AUTO_SCAN_RETAILER,
        )
        report = AutoScanReport(
            guild_id=guild.guild_id,
            allowed=True,
            settings=settings,
            products_checked=products_checked,
            searches_checked=searches_checked,
            total_cards=len(unique_cards),
            fresh_cards=len(fresh_selection.fresh),
            cards_attempted_for_public=len(shown_cards),
            used_repeat_fallback=used_repeat_fallback,
            repeat_summary=fresh_selection.summary_line(),
            public_result=result,
            warnings=tuple(warnings),
        )
        log.info("Auto-scan completed %s reason=%s", report.log_fields(), compact_log_text(explain_public_post_result(result)))
        return report


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


def compact_log_text(value: str) -> str:
    return " | ".join(str(value).splitlines())[:500]
