from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from sniperplug.cogs.deal_scanner import DealCard, HuntPreset, provider_health_error_message, safe_defer, safe_send_interaction
from sniperplug.cogs.public_alerts import auto_scan_allowed, record_auto_scan_run
from sniperplug.services.autoscan_history import save_autoscan_report
from sniperplug.services.autoscan_observed_price_memory import run_autoscan_verified_category_with_observed_memory
from sniperplug.services.autoscan_decision_trail import explain_autoscan_decision_trail, no_post_plain_english
from sniperplug.services.deal_confidence import DEFAULT_AUTOSCAN_CONFIDENCE_FLOOR, select_confident_public_cards
from sniperplug.services.deal_category_preferences import apply_category_preferences, get_category_preferences
from sniperplug.services.deal_feedback import apply_feedback_learning_to_cards
from sniperplug.services.deal_finder_telemetry import top_route_lines
from sniperplug.services.deal_search_modes import MODE_BEST, rank_for_search_mode
from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_deal_posts import PublicPostResult, maybe_post_public_deal_cards
from sniperplug.services.scout_lane_polish import polish_public_scout_card, scout_rank
from sniperplug.services.public_result_explainer import explain_public_post_result
from sniperplug.services.public_deal_quality import select_public_deal_candidates
from sniperplug.services.setup_self_heal import repair_public_alert_setup
from sniperplug.services.verified_discount_hunt import HUNT_PRESETS, VerifiedHuntResult, collect_verified_discount_cards


log = logging.getLogger("sniperplug.autoscan")
AUTO_SCAN_INTERVAL_MINUTES = 15
AUTO_SCAN_RETAILER = "walmart"
AUTO_SCAN_SOURCE_LABEL = "autoscan:walmart_discovery"
AUTO_SCAN_PUBLIC_LIMIT = 5
AUTO_SCAN_REVIEW_FALLBACK_LIMIT = 3
AUTO_SCAN_CATEGORY_ROTATION = ("deal_week", "deal_week", "all", "deal_week", "tech", "deal_week", "essentials", "deal_week", "auto_tools", "deal_week", "beauty", "home")
AUTO_SCAN_PUBLIC_MODE = MODE_BEST
AUTOSCAN_CONFIDENCE_FLOOR = DEFAULT_AUTOSCAN_CONFIDENCE_FLOOR
AUTO_SCAN_SCHEDULED_QUERY_COUNT = 4
AUTO_SCAN_MANUAL_QUERY_COUNT = 8
AUTO_SCAN_PROGRESS_SECONDS = 45
AUTO_SCAN_MAX_CONCURRENCY = 1
_AUTOSCAN_LOCKS: dict[int, asyncio.Lock] = {}


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
    category_key: str = ""
    category_label: str = ""
    min_discount: int = 0
    public_mode: str = "Best Picks"
    confidence_floor: int = AUTOSCAN_CONFIDENCE_FLOOR
    confidence_summary: str = ""
    feedback_learning_summary: str = ""
    verification_failure_summary: str = ""
    review_candidate_summary: str = ""
    decision_trail_summary: str = ""
    route_summary: str = ""
    price_memory_summary: str = ""
    products_checked: int = 0
    searches_checked: int = 0
    total_cards: int = 0
    verified_before_memory: int = 0
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
            "category": self.category_key,
            "category_label": self.category_label,
            "threshold": self.min_discount,
            "public_mode": self.public_mode,
            "confidence_floor": self.confidence_floor,
            "confidence_summary": compact_log_text(self.confidence_summary),
            "feedback_learning": compact_log_text(self.feedback_learning_summary),
            "verification_failure_summary": compact_log_text(self.verification_failure_summary, limit=900),
            "review_candidate_summary": compact_log_text(self.review_candidate_summary, limit=900),
            "decision_trail_summary": compact_log_text(self.decision_trail_summary, limit=1200),
            "route_summary": compact_log_text(self.route_summary, limit=900),
            "price_memory_summary": compact_log_text(self.price_memory_summary, limit=500),
            "checked": self.products_checked,
            "searches": self.searches_checked,
            "total_cards": self.total_cards,
            "verified_before_memory": self.verified_before_memory,
            "fresh_cards": self.fresh_cards,
            "public_attempt": self.cards_attempted_for_public,
            "repeat_fallback": self.used_repeat_fallback,
            "repeat_summary": compact_log_text(self.repeat_summary),
            "posted": self.public_result.posted,
            "dupes": self.public_result.skipped_duplicate,
            "recent_dupes": getattr(self.public_result, "skipped_recent_alert_duplicate", 0),
            "reserved_dupes": getattr(self.public_result, "skipped_reserved_duplicate", 0),
            "not_alertable": self.public_result.skipped_not_alertable,
            "disabled": self.public_result.skipped_disabled,
            "errors": tuple(clean_log_text(error) for error in self.public_result.errors),
            "settings": self.settings or {},
            "warnings": self.warnings[:3],
        }

    def discord_summary(self) -> str:
        if not self.allowed:
            return f"Auto-scan did not run: {self.reason}"
        result = self.public_result
        category = f"{self.category_label or self.category_key or 'unknown'}"
        confidence = f"\nConfidence: **{self.confidence_floor}/100 floor** • {self.confidence_summary}" if self.confidence_summary else f"\nConfidence: **{self.confidence_floor}/100 floor**"
        feedback = f"\nLearning: {self.feedback_learning_summary}" if self.feedback_learning_summary else ""
        verification = f"\nVerification trail: {self.verification_failure_summary}" if self.verification_failure_summary else ""
        memory = f"\nPrice memory: {self.price_memory_summary}" if self.price_memory_summary else ""
        duplicate_breakdown = duplicate_breakdown_text(result)
        plain = no_post_plain_english(
            verified_count=self.total_cards,
            public_candidate_count=self.cards_attempted_for_public,
            fresh_count=self.fresh_cards,
            posted_count=result.posted,
        )
        return (
            f"Category: **{category}**\n"
            f"Threshold: **{self.min_discount}%+ verified markdown** • Ranking: **{self.public_mode}**{confidence}{feedback}{memory}{verification}\n"
            f"Checked **{self.products_checked}** products across **{self.searches_checked}** searches.\n"
            f"Verified before memory: **{self.verified_before_memory}** • Verified after memory/ranking: **{self.total_cards}** • Fresh/new/lower-price: **{self.fresh_cards}** • Sent to public guard: **{self.cards_attempted_for_public}**\n"
            f"Posted: **{result.posted}** • Duplicates blocked: **{result.skipped_duplicate}**{duplicate_breakdown} • Not alertable: **{result.skipped_not_alertable}** • Disabled: **{result.skipped_disabled}**\n"
            f"Bottom line: {plain}\n"
            "Setup note: green setup means SniperPlug can post; the finder still needs verified Walmart markdown proof before a deal is public-ready. Scout/review leads stay private.\n"
            f"Private review fallback found: **{'yes' if self.used_repeat_fallback else 'no'}**\n"
            f"Fresh filter: {self.repeat_summary or 'n/a'}"
        )


class AutoScanRunnerCog(commands.Cog):
    """Runs enabled retailer auto-discovery in the background."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._background_tasks: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        self.auto_scan_loop.start()

    async def cog_unload(self) -> None:
        self.auto_scan_loop.cancel()

    @app_commands.command(name="autoscan_now", description="Run the Walmart auto-scan now and show why it did or did not post.")
    @app_commands.describe(force="Bypass interval/daily gate for this manual test. Public alerts still must be configured.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autoscan_now(self, interaction: discord.Interaction, force: bool = True) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Use this in a server so I know which auto-scan settings to test.", ephemeral=True)
            return

        if not await safe_defer(interaction, ephemeral=True, thinking=True):
            log.warning(
                "Manual /autoscan_now could not acknowledge before Discord expired the interaction guild=%s user=%s",
                interaction.guild_id,
                getattr(interaction.user, "id", None),
            )
            return

        guild_id = int(interaction.guild_id)
        lock = autoscan_lock(guild_id)
        if lock.locked():
            await interaction.followup.send(
                "Auto-scan is already running for this server. I blocked the duplicate run so the bot does not hang or double-post.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "✅ Auto-scan accepted instantly. I’m running the Walmart scan now and will send the result when it finishes. Duplicate clicks are blocked.",
            ephemeral=True,
        )
        task = asyncio.create_task(self._run_autoscan_now_background(interaction, guild_id, force))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_autoscan_now_background(self, interaction: discord.Interaction, guild_id: int, force: bool) -> None:
        lock = autoscan_lock(guild_id)
        async with lock:
            try:
                target_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
                repair = await repair_public_alert_setup(self.bot.db, self.bot, guild_id, target_channel=target_channel)
                config = repair.config if repair.config is not None else await get_public_alert_config(self.bot.db, guild_id)
                if repair.human_action_required:
                    await self._safe_autoscan_followup(
                        interaction,
                        "SniperPlug could not safely repair the saved posting setup yet. "
                        + repair.discord_line()
                        + "\n\nThis should only require setup on first install or after channel permissions/deletion changed.",
                    )
                    return
                if not config.get("enabled") or not config.get("channel_id"):
                    await self._safe_autoscan_followup(
                        interaction,
                        "Public alerts are still missing after self-heal. Run `/autoscan_health` for the exact channel/permission blocker.",
                    )
                    return
                if AUTO_SCAN_RETAILER not in set(config.get("retailers") or ()):
                    await self._safe_autoscan_followup(
                        interaction,
                        "Walmart was still missing from public retailers after self-heal. Run `/autoscan_health` so the repair status is visible.",
                    )
                    return

                progress_task = asyncio.create_task(self._autoscan_progress_notice(interaction))
                try:
                    report = await self._run_guild_walmart_discovery(
                        AutoScanGuild(guild_id, config.get("channel_id")),
                        force=force,
                        query_count_override=AUTO_SCAN_MANUAL_QUERY_COUNT if force else None,
                        report_label="Manual pass",
                    )
                finally:
                    progress_task.cancel()
                await self._send_autoscan_report(interaction, report, label="Manual scan result")
            except Exception as exc:
                log.exception("Manual /autoscan_now failed guild=%s", guild_id)
                await self._safe_autoscan_followup(
                    interaction,
                    f"Auto-scan hit an error after starting: `{clean_log_text(exc)}`",
                )

    async def _send_autoscan_report(self, interaction: discord.Interaction, report: AutoScanReport, *, label: str = "Auto-scan test result") -> None:
        embed = discord.Embed(
            title=f"🧭 {label}",
            description=report.discord_summary()[:4000],
            color=discord.Color.green() if report.public_result.posted else discord.Color.orange(),
        )
        if report.review_candidate_summary:
            embed.add_field(
                name="Review-only diagnostics",
                value=trim_discord_value(report.review_candidate_summary),
                inline=False,
            )
        if report.decision_trail_summary:
            embed.add_field(
                name="Candidate decision trail",
                value=trim_discord_value(report.decision_trail_summary),
                inline=False,
            )
        if report.route_summary:
            embed.add_field(
                name="Top search routes",
                value=trim_discord_value(report.route_summary),
                inline=False,
            )
        if report.warnings:
            warning_text = chr(10).join(f"• {warning}" for warning in report.warnings[:5])
            embed.add_field(name="Warnings", value=warning_text, inline=False)
        if not report.public_result.posted:
            embed.add_field(
                name="Why nothing posted",
                value=trim_discord_value(autoscan_blocker_summary(report)),
                inline=False,
            )
        if report.public_result.errors:
            error_text = chr(10).join(f"• {clean_log_text(error)}" for error in report.public_result.errors[:5])
            embed.add_field(name="Errors", value=error_text, inline=False)
        embed.set_footer(text="This uses the same direct verified auto-scan path as the background loop.")
        await self._safe_autoscan_followup(interaction, embed=embed)

    async def _autoscan_progress_notice(self, interaction: discord.Interaction) -> None:
        try:
            await asyncio.sleep(AUTO_SCAN_PROGRESS_SECONDS)
            await self._safe_autoscan_followup(
                interaction,
                "⏳ Still scanning Walmart. Fast results are taking longer than usual, but the scan is alive. I’ll post the report as soon as the first pass finishes.",
            )
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Failed to send /autoscan_now progress notice")

    async def _safe_autoscan_followup(
        self,
        interaction: discord.Interaction,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
    ) -> None:
        try:
            await interaction.followup.send(content=content, embed=embed, ephemeral=True)
            return
        except (discord.NotFound, discord.HTTPException) as exc:
            if interaction_token_is_gone(exc):
                if await self._send_autoscan_dm_fallback(interaction, content=content, embed=embed):
                    log.info("Sent /autoscan_now result by DM because Discord expired the interaction token")
                    return
                log.warning("Could not send /autoscan_now result; interaction token expired and DM fallback failed: %s", clean_log_text(exc))
                return
            log.exception("Failed to send /autoscan_now followup")
        except Exception:
            log.exception("Failed to send /autoscan_now followup")

    async def _send_autoscan_dm_fallback(
        self,
        interaction: discord.Interaction,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
    ) -> bool:
        user = getattr(interaction, "user", None)
        send = getattr(user, "send", None)
        if not callable(send):
            return False
        try:
            prefix = "SniperPlug finished your auto-scan after Discord expired the private command window."
            if content and embed is None:
                await send(prefix + "\n\n" + str(content)[:1800])
            else:
                await send(prefix, embed=embed)
            return True
        except Exception:
            return False


    @autoscan_now.error
    async def autoscan_now_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to run an auto-scan test."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Auto-scan test hit an error: `{clean_log_text(error)}`"
        )
        sent = await safe_send_interaction(interaction, message, ephemeral=True)
        if not sent:
            log.warning("Could not send /autoscan_now error because Discord interaction already expired: %s", clean_log_text(error))



    @tasks.loop(minutes=AUTO_SCAN_INTERVAL_MINUTES)
    async def auto_scan_loop(self) -> None:
        await self.bot.wait_until_ready()
        guilds = await list_public_alert_guilds(self.bot.db, bot=self.bot)
        if not guilds:
            return

        health_error = await provider_health_error_message()
        if health_error:
            log.info("Auto-scan skipped: %s", health_error)
            return

        semaphore = asyncio.Semaphore(max(1, AUTO_SCAN_MAX_CONCURRENCY))
        tasks_for_guilds = [
            asyncio.create_task(self._run_scheduled_guild(guild, semaphore))
            for guild in guilds
        ]
        results = await asyncio.gather(*tasks_for_guilds, return_exceptions=True)
        for guild, result in zip(guilds, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                log.error(
                    "Auto-scan isolated guild task escaped its guard guild=%s error=%s",
                    guild.guild_id,
                    clean_log_text(result),
                )

    async def _run_scheduled_guild(
        self,
        guild: AutoScanGuild,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            lock = autoscan_lock(guild.guild_id)
            if lock.locked():
                log.info("Auto-scan skipped guild=%s because another auto-scan is already running", guild.guild_id)
                return
            async with lock:
                try:
                    await self._run_guild_walmart_discovery(guild)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Auto-scan guild run failed but loop will continue; other guild tasks are isolated guild=%s", guild.guild_id)

    @auto_scan_loop.before_loop
    async def before_auto_scan_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _run_guild_walmart_discovery(self, guild: AutoScanGuild, *, force: bool = False, query_count_override: int | None = None, report_label: str = "") -> AutoScanReport:
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
                report = AutoScanReport(guild_id=guild.guild_id, allowed=False, reason=reason, settings=settings)
                await persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
                log.debug("Auto-scan blocked guild=%s retailer=%s reason=%s", guild.guild_id, AUTO_SCAN_RETAILER, reason)
                return report
        else:
            settings = {"forced": True, "retailer": AUTO_SCAN_RETAILER}

        preset = select_autoscan_preset(guild.guild_id, force=force, query_count_override=query_count_override)
        result = await run_autoscan_verified_category(self.bot.db, guild.guild_id, preset=preset)
        warnings = clean_warning_list(result.warnings)
        if report_label:
            warnings.append(f"{report_label}: scanned **{len(preset.queries)}** route(s) in this pass.")
        diagnostics = autoscan_diagnostics(result)

        unique_cards = dedupe_cards(result.cards)
        unique_cards = rank_for_search_mode(unique_cards, [], AUTO_SCAN_PUBLIC_MODE, limit=max(len(unique_cards), AUTO_SCAN_PUBLIC_LIMIT)).verified
        unique_cards = await apply_feedback_learning_to_cards(self.bot.db, guild_id=guild.guild_id, cards=unique_cards, fallback_retailer=AUTO_SCAN_RETAILER)
        feedback_summary = summarize_feedback_learning(unique_cards)
        confidence_selection = select_confident_public_cards(unique_cards, floor=AUTOSCAN_CONFIDENCE_FLOOR)
        public_candidates = list(confidence_selection.cards)
        rescued_cards = select_public_deal_candidates(
            unique_cards,
            source_label=f"{AUTO_SCAN_SOURCE_LABEL}:{preset.key}",
            min_discount=result.min_discount,
            limit=AUTO_SCAN_PUBLIC_LIMIT,
        )
        added_rescues = 0
        for card in rescued_cards:
            if card not in public_candidates:
                public_candidates.append(card)
                added_rescues += 1
        if added_rescues:
            warnings.append(
                f"Verified markdown rescue lane added **{added_rescues}** real threshold-matching deal(s) that confidence scoring would have hidden."
            )

        fresh_selection = await select_fresh_deal_cards(
            self.bot.db,
            guild_id=guild.guild_id,
            cards=public_candidates,
            fallback_retailer=AUTO_SCAN_RETAILER,
            limit=AUTO_SCAN_PUBLIC_LIMIT,
            hide_active_cache_repeats=False,
            min_public_discount=result.min_discount,
            source_label=f"{AUTO_SCAN_SOURCE_LABEL}:{preset.key}",
        )
        decision_trail_summary = explain_autoscan_decision_trail(
            all_verified_cards=unique_cards,
            confidence_cards=list(confidence_selection.cards),
            public_candidates=public_candidates,
            fresh_cards=list(fresh_selection.fresh),
            min_discount=result.min_discount,
            confidence_floor=AUTOSCAN_CONFIDENCE_FLOOR,
            limit=8,
        )

        shown_cards = fresh_selection.fresh
        watchlist_cards: list[DealCard] = []
        if not shown_cards:
            watchlist_cards = prepare_review_watchlist_cards(result, limit=AUTO_SCAN_REVIEW_FALLBACK_LIMIT)
            if watchlist_cards:
                warnings.append(
                    "No verified public deal passed. Public Scout Lane only posts high-confidence leads with hard value proof. Weak reference-only leads stay private."
                )

        if not force:
            await record_auto_scan_run(self.bot.db, guild.guild_id, AUTO_SCAN_RETAILER, scan_key=scan_key)

        if not shown_cards:
            if watchlist_cards:
                warnings.append(
                    "Public Scout Lane is disabled for public posts. Review/scout leads stay private unless they meet the verified API markdown threshold. Scout Lane public post: never; scout leads are private diagnostics."
                )

            private_watchlist_count = len(watchlist_cards)
            public_result = PublicPostResult()
            report = AutoScanReport(
                guild_id=guild.guild_id,
                allowed=True,
                reason="Auto-scan completed with no new/lower-price API-verified public deals that met the threshold.",
                settings=settings,
                category_key=preset.key,
                category_label=preset.label,
                min_discount=result.min_discount,
                public_mode="Verified API Threshold Only",
                confidence_floor=AUTOSCAN_CONFIDENCE_FLOOR,
                confidence_summary=confidence_selection.summary_line(),
                feedback_learning_summary=feedback_summary,
                verification_failure_summary=diagnostics["verification_failure_summary"],
                review_candidate_summary=diagnostics["review_candidate_summary"],
                decision_trail_summary=decision_trail_summary,
                route_summary=diagnostics["route_summary"],
                price_memory_summary=diagnostics["price_memory_summary"],
                products_checked=result.products_checked,
                searches_checked=result.searches_attempted,
                total_cards=len(unique_cards),
                verified_before_memory=result.total_verified_cards,
                fresh_cards=0,
                cards_attempted_for_public=0,
                used_repeat_fallback=bool(watchlist_cards),
                repeat_summary=(
                    f"{fresh_selection.summary_line()} • private review/scout leads kept out of public: "
                    f"**{private_watchlist_count}**"
                ),
                public_result=public_result,
                warnings=tuple(warnings),
            )
            await persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
            log.info("Auto-scan completed with no API-verified public threshold deal %s", report.log_fields())
            return report

        category_preferences = await get_category_preferences(self.bot.db, guild.guild_id)
        shown_cards, category_suppressed_cards, category_notes = apply_category_preferences(shown_cards, category_preferences)
        if category_suppressed_cards:
            warnings.append(
                f"Category preferences suppressed **{len(category_suppressed_cards)}** normal lead(s). Extreme/nuclear deals still override muted categories."
            )
        for note in category_notes[:4]:
            if note not in warnings:
                warnings.append(note)

        public_result = await maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=guild.guild_id,
            cards=shown_cards,
            source_label=f"{AUTO_SCAN_SOURCE_LABEL}:{preset.key}{':watchlist' if watchlist_cards else ''}",
            fallback_retailer=AUTO_SCAN_RETAILER,
            min_public_discount=result.min_discount,
        )
        report = AutoScanReport(
            guild_id=guild.guild_id,
            allowed=True,
            settings=settings,
            category_key=preset.key,
            category_label=preset.label,
            min_discount=result.min_discount,
            public_mode="Best Picks",
            confidence_floor=AUTOSCAN_CONFIDENCE_FLOOR,
            confidence_summary=confidence_selection.summary_line(),
            feedback_learning_summary=feedback_summary,
            verification_failure_summary=diagnostics["verification_failure_summary"],
            review_candidate_summary=diagnostics["review_candidate_summary"],
            decision_trail_summary=decision_trail_summary,
            route_summary=diagnostics["route_summary"],
            price_memory_summary=diagnostics["price_memory_summary"],
            products_checked=result.products_checked,
            searches_checked=result.searches_attempted,
            total_cards=len(unique_cards),
            verified_before_memory=result.total_verified_cards,
            fresh_cards=len(fresh_selection.fresh),
            cards_attempted_for_public=len(shown_cards),
            used_repeat_fallback=bool(watchlist_cards),
            repeat_summary=watchlist_repeat_summary(fresh_selection.summary_line(), watchlist_cards, public_result),
            public_result=public_result,
            warnings=tuple(warnings),
        )
        await persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
        log.info("Auto-scan completed %s reason=%s", report.log_fields(), compact_log_text(explain_public_post_result(public_result)))
        return report


def select_autoscan_preset(guild_id: int, *, force: bool = False, query_count_override: int | None = None) -> HuntPreset:
    """Pick the Walmart auto-scan route.

    Manual `/autoscan_now force:true` always starts with Deal Week so testing
    does not randomly land on a weak category. Scheduled runs still rotate, but
    Deal Week appears often and `all` is included for broader coverage.
    """
    if force:
        base = HUNT_PRESETS.get("deal_week") or HUNT_PRESETS.get("all") or next(iter(HUNT_PRESETS.values()))
    elif not AUTO_SCAN_CATEGORY_ROTATION:
        base = HUNT_PRESETS["tech"]
    else:
        bucket = int(time.time() // (AUTO_SCAN_INTERVAL_MINUTES * 60))
        index = (bucket + int(guild_id)) % len(AUTO_SCAN_CATEGORY_ROTATION)
        key = AUTO_SCAN_CATEGORY_ROTATION[index]
        base = HUNT_PRESETS.get(key) or next(iter(HUNT_PRESETS.values()))

    if query_count_override is not None:
        query_count = max(1, int(query_count_override))
    elif force:
        query_count = AUTO_SCAN_MANUAL_QUERY_COUNT
    else:
        query_count = AUTO_SCAN_SCHEDULED_QUERY_COUNT

    queries = rotated_query_slice(base.queries, guild_id=guild_id, query_count=query_count)
    return HuntPreset(
        base.key,
        base.label,
        base.emoji,
        f"{base.description} Auto-scan slice preserves coverage over rotation.",
        queries,
        base.min_discount,
    )


def rotated_query_slice(queries: tuple[str, ...], *, guild_id: int, query_count: int) -> tuple[str, ...]:
    if not queries:
        return queries
    count = max(1, min(int(query_count), len(queries)))
    bucket = int(time.time() // (AUTO_SCAN_INTERVAL_MINUTES * 60))
    start = ((bucket + int(guild_id)) * count) % len(queries)
    rotated = [queries[(start + offset) % len(queries)] for offset in range(count)]
    return tuple(rotated)


async def run_autoscan_verified_category(db, guild_id: int, *, preset: HuntPreset) -> VerifiedHuntResult:
    """Run the bounded autoscan collector while retaining exact-item price observations.

    Scheduled scans stay lightweight (two pages per route, bounded concurrency and
    capped observation writes), but they must keep trustworthy historical prices.
    Otherwise every scan starts from zero and observed-price-drop proof can never
    mature into a verified public deal.
    """
    return await run_autoscan_verified_category_with_observed_memory(
        db,
        guild_id,
        preset=preset,
    )


async def persist_autoscan_report(db, report: AutoScanReport, *, scan_key: str) -> None:
    try:
        await save_autoscan_report(
            db,
            guild_id=report.guild_id,
            retailer=AUTO_SCAN_RETAILER,
            scan_key=scan_key,
            payload=report.log_fields(),
        )
    except Exception as exc:
        log.warning("Failed to persist auto-scan report guild=%s: %s", report.guild_id, exc)



def prepare_review_watchlist_cards(result: VerifiedHuntResult, *, limit: int = AUTO_SCAN_REVIEW_FALLBACK_LIMIT) -> list[DealCard]:
    """Promote the best review/scout cards into a clearly labeled private watchlist.

    Public Scout Lane is disabled for public posts. This function is only for
    private diagnostics when strict verified public posting finds nothing.
    """
    review = result.review_candidates
    if review is None or not review.cards:
        return []

    ranked_review_cards = sorted(
        list(review.cards),
        key=lambda card: scout_rank(card, min_discount=result.min_discount),
        reverse=True,
    )[: max(1, int(limit))]

    cards: list[DealCard] = []
    for position, card in enumerate(ranked_review_cards, start=1):
        rank = max(scout_rank(card, min_discount=result.min_discount), 95)
        card = polish_public_scout_card(card, rank=rank, min_discount=result.min_discount, position=position)

        key = (
            getattr(card, "selected_offer_id", None)
            or getattr(card, "sku", None)
            or getattr(card, "upc", None)
            or getattr(card, "url", "")
            or getattr(card, "label", "watchlist")
        )
        price = getattr(card, "current_price", None)
        setattr(card, "public_post_key", f"watchlist:{key}:price:{price}")
        setattr(card, "should_alert", True)

        embed = getattr(card, "embed", None)
        if isinstance(embed, discord.Embed):
            title = str(embed.title or "Walmart watchlist lead")
            if not title.startswith("🟨 Watchlist"):
                embed.title = trim_text(f"🟨 Watchlist • {title}", 256)
            if not any(str(field.name or "") == "🟨 Walmart Deal Week Watchlist" for field in embed.fields):
                embed.add_field(
                    name="🟨 Walmart Deal Week Watchlist",
                    value=(
                        f"Strict public threshold is **{result.min_discount}%+**, but this was one of the strongest review/flip/scout leads.\n"
                        "This is a private wake-up candidate, not a blind-buy guarantee. Verify Walmart app price, selected option, seller, and stock before buying/posting."
                    ),
                    inline=False,
                )
        cards.append(card)
    return cards

def watchlist_repeat_summary(base_summary: str, watchlist_cards: list[DealCard], public_result: PublicPostResult | None = None) -> str:
    if not watchlist_cards:
        return base_summary
    if public_result is None:
        return f"{base_summary} • Deal Week watchlist fallback selected **{len(watchlist_cards)}** review lead(s)"
    blocked = int(public_result.skipped_duplicate or 0) + int(public_result.skipped_not_alertable or 0) + int(public_result.skipped_disabled or 0) + int(public_result.skipped_wrong_retailer or 0)
    return (
        f"{base_summary} • Deal Week watchlist fallback selected **{len(watchlist_cards)}** review lead(s) "
        f"• public posted **{public_result.posted}** • public blocked **{blocked}**"
    )


def interaction_token_is_gone(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    text = str(exc).lower()
    return code in {10062, 50027} or "unknown interaction" in text or "invalid webhook token" in text


def autoscan_lock(guild_id: int) -> asyncio.Lock:
    lock = _AUTOSCAN_LOCKS.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _AUTOSCAN_LOCKS[guild_id] = lock
    return lock


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


def summarize_feedback_learning(cards: list[DealCard]) -> str:
    adjusted = [int(getattr(card, "feedback_learning_score", 0) or 0) for card in cards]
    boosted = sum(1 for value in adjusted if value > 0)
    penalized = sum(1 for value in adjusted if value < 0)
    if not boosted and not penalized:
        return "no saved feedback adjustments yet"
    return f"boosted **{boosted}** • penalized **{penalized}** from saved feedback"


def autoscan_diagnostics(result: VerifiedHuntResult) -> dict[str, str]:
    review = result.review_candidates
    review_summary = review.summary_line() if review else "review diagnostics unavailable"
    route_lines = top_route_lines(result.route_stats, limit=4)
    route_summary = "\n".join(route_lines) if route_lines else "No route stats saved for this run."
    price_memory_summary = summarize_price_memory(result)
    return {
        "verification_failure_summary": build_verification_failure_summary(result),
        "review_candidate_summary": review_summary,
        "route_summary": route_summary,
        "price_memory_summary": price_memory_summary,
    }


def build_verification_failure_summary(result: VerifiedHuntResult) -> str:
    review = result.review_candidates
    verified_before_memory = int(result.total_verified_cards or 0)
    verified_after_memory = len(result.cards or [])
    if verified_before_memory > 0 and verified_after_memory == 0:
        return "Verified markdown cards existed, but price-memory/fresh filtering hid same-price repeats. A lower-price repeat can still post again."
    if verified_before_memory > 0:
        return "Verified markdown cards existed; later gates decide public posting: feedback ranking, confidence floor, fresh/lower-price filter, duplicate guard, and public-alert proof."
    if review is None:
        return "0 verified markdown cards. Review diagnostics were not available for this run."

    blockers = [
        ("missing trusted was/reference", int(review.missing_reference_count or 0)),
        ("weak/ignored reference", int(review.weak_reference_count or 0)),
        ("under threshold", int(review.under_threshold_count or 0)),
        ("missing current price", int(review.missing_current_count or 0)),
        ("no coupon/cash/comp/value signal", int(review.no_value_signal_count or 0)),
        ("bad coupon/cash/value rejected", int(review.rejected_bad_value_count or 0)),
    ]
    top = [(label, count) for label, count in sorted(blockers, key=lambda item: item[1], reverse=True) if count > 0][:4]
    if not top:
        return "0 verified markdown cards. Walmart returned products, but none produced trusted markdown proof at the current threshold."
    bits = " • ".join(f"{label}: **{count}**" for label, count in top)
    extras = []
    if review.cards:
        extras.append(f"review-only leads: **{len(review.cards)}**")
    if review.exact_match_count:
        extras.append(f"exact-match rescues: **{review.exact_match_count}**")
    extra_text = f" • {' • '.join(extras)}" if extras else ""
    return f"0 verified markdown cards. Main blockers: {bits}{extra_text}"


def summarize_price_memory(result: VerifiedHuntResult) -> str:
    memory = result.price_memory
    if memory is None:
        return "not used"
    try:
        checked = len(getattr(memory, "decisions", []) or [])
        shown = len(getattr(memory, "shown", []) or [])
        summary = memory.summary_line() if hasattr(memory, "summary_line") else "price memory used"
        examples: list[str] = []
        for decision in getattr(memory, "decisions", []) or []:
            if not getattr(decision, "should_show", False):
                continue
            card = getattr(decision, "card", None)
            label = trim_text(str(getattr(card, "label", None) or "deal"), 42)
            status = str(getattr(decision, "status", "shown")).replace("_", " ")
            examples.append(f"{label} ({status})")
            if len(examples) >= 2:
                break
        example_text = f" • examples: {', '.join(examples)}" if examples else ""
        return f"checked **{checked}** verified cards • showing **{shown}** • {summary}{example_text}"
    except Exception as exc:
        log.warning("Failed to summarize price memory safely: %s", exc)
        return "used, but summary unavailable"


async def delete_ghost_public_alert_guild_row(db, guild_id: int) -> None:
    conn = db.require_conn()
    tables = (
        "guild_public_alert_settings",
        "guild_retailer_auto_scan_settings",
        "guild_retailer_auto_scan_runs",
        "guild_alert_channels",
        "guild_public_deal_posts",
        "guild_active_deal_cache",
        "alert_dedupe",
    )
    for table in tables:
        try:
            await conn.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        except Exception:
            # Table may not exist yet on older deployments.
            pass
    try:
        await conn.commit()
    except Exception:
        pass


async def list_public_alert_guilds(db, *, bot: Any | None = None) -> list[AutoScanGuild]:
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT guild_id FROM guild_public_alert_settings WHERE enabled = 1 AND channel_id IS NOT NULL"
    )
    rows = await cursor.fetchall()
    guilds: list[AutoScanGuild] = []
    seen: set[int] = set()

    live_guild_ids: set[int] | None = None
    if bot is not None:
        live_guild_ids = {int(guild.id) for guild in list(getattr(bot, "guilds", []) or [])}

    for row in rows:
        try:
            raw_guild_id = row["guild_id"]
        except Exception:
            try:
                raw_guild_id = row[0]
            except Exception:
                log.warning("Auto-scan skipped malformed public-alert row without guild_id: %r", row)
                continue
        try:
            guild_id = int(raw_guild_id)
        except (TypeError, ValueError):
            log.warning("Auto-scan skipped malformed public-alert guild id: %r", raw_guild_id)
            continue

        if live_guild_ids is not None and guild_id not in live_guild_ids:
            log.warning(
                "Auto-scan deleted stale/ghost public-alert guild row guild=%s live_guilds=%s. Run /setup_sniperplug_here in the live server if this server still needs alerts.",
                guild_id,
                sorted(live_guild_ids),
            )
            try:
                await delete_ghost_public_alert_guild_row(db, guild_id)
            except Exception:
                log.exception("Auto-scan failed to clean stale guild row but discovery will continue guild=%s", guild_id)
            continue

        try:
            config = await get_public_alert_config(db, guild_id)
        except Exception:
            log.exception("Auto-scan skipped guild because public-alert config could not be read guild=%s", guild_id)
            continue
        if AUTO_SCAN_RETAILER not in set(config.get("retailers") or ()) or not config.get("channel_id"):
            continue
        try:
            channel_id = int(config["channel_id"])
        except (TypeError, ValueError):
            log.warning("Auto-scan skipped guild with malformed public-alert channel id guild=%s channel=%r", guild_id, config.get("channel_id"))
            continue
        if guild_id in seen:
            continue
        seen.add(guild_id)
        guilds.append(AutoScanGuild(guild_id=guild_id, channel_id=channel_id))
    return guilds



def autoscan_blocker_summary(report: AutoScanReport) -> str:
    result = report.public_result
    lines: list[str] = []
    if report.total_cards <= 0:
        lines.append("No verified cards were created. Most likely reason: Walmart API did not return trusted was/typical price proof at this threshold.")
    elif report.cards_attempted_for_public <= 0:
        lines.append("Verified cards existed, but none reached final public preflight.")
    elif result.posted <= 0:
        if result.skipped_disabled:
            lines.append("Public alerts were disabled or missing a saved channel.")
        if result.skipped_wrong_retailer:
            lines.append(f"{result.skipped_wrong_retailer} card(s) were blocked because Walmart is not allowed in public stores.")
        if result.skipped_duplicate:
            lines.append(f"{result.skipped_duplicate} card(s) were blocked as recent/exact duplicates.")
        if result.skipped_not_alertable:
            lines.append(f"{result.skipped_not_alertable} card(s) failed public-quality/not-alertable checks.")
        if result.errors:
            lines.append("Posting errors: " + "; ".join(clean_log_text(error) for error in result.errors[:3]))
    return "\n".join(f"• {line}" for line in lines) or "No blocker summary available."

def duplicate_breakdown_text(result: PublicPostResult) -> str:
    recent = int(getattr(result, "skipped_recent_alert_duplicate", 0) or 0)
    reserved = int(getattr(result, "skipped_reserved_duplicate", 0) or 0)
    pieces: list[str] = []
    if recent:
        pieces.append(f"recent posted same/higher: **{recent}**")
    if reserved:
        pieces.append(f"active reservation: **{reserved}**")
    return f" ({' • '.join(pieces)})" if pieces else ""


def clean_warning_list(values: list[str] | tuple[str, ...]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = clean_log_text(value, limit=220)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if len(cleaned) >= 5:
            break
    return cleaned


def clean_log_text(value: Any, *, limit: int = 220) -> str:
    text = str(value or "")
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit].rstrip() + ("…" if len(cleaned) > limit else "")


def compact_log_text(value: str, *, limit: int = 500) -> str:
    return " | ".join(str(value).splitlines())[:limit]


def trim_discord_value(value: str, *, limit: int = 1024) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text or "n/a"
    return text[: limit - 1].rstrip() + "…"


def trim_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

# Decision trail wording: public-quality cards that passed final posting gates.
