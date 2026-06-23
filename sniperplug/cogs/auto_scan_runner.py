from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from sniperplug.cogs.deal_scanner import DealCard, HuntPreset, provider_health_error_message
from sniperplug.cogs.public_alerts import auto_scan_allowed, record_auto_scan_run
from sniperplug.services.autoscan_history import save_autoscan_report
from sniperplug.services.deal_confidence import DEFAULT_AUTOSCAN_CONFIDENCE_FLOOR, select_confident_public_cards
from sniperplug.services.deal_feedback import apply_feedback_learning_to_cards
from sniperplug.services.deal_finder_telemetry import top_route_lines
from sniperplug.services.deal_search_modes import MODE_BEST, rank_for_search_mode
from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_deal_posts import PublicPostResult, maybe_post_public_deal_cards
from sniperplug.services.public_result_explainer import explain_public_post_result
from sniperplug.services.verified_discount_hunt import HUNT_PRESETS, VerifiedHuntResult, collect_verified_discount_cards


log = logging.getLogger("sniperplug.autoscan")
AUTO_SCAN_INTERVAL_MINUTES = 15
AUTO_SCAN_RETAILER = "walmart"
AUTO_SCAN_SOURCE_LABEL = "autoscan:walmart_discovery"
AUTO_SCAN_PUBLIC_LIMIT = 5
AUTO_SCAN_REVIEW_FALLBACK_LIMIT = 3
AUTO_SCAN_CATEGORY_ROTATION = ("deal_week", "tech", "deal_week", "beauty", "home", "deal_week", "toys", "auto_tools", "essentials")
AUTO_SCAN_PUBLIC_MODE = MODE_BEST
AUTOSCAN_CONFIDENCE_FLOOR = DEFAULT_AUTOSCAN_CONFIDENCE_FLOOR
AUTO_SCAN_FAST_QUERY_COUNT = 4
AUTO_SCAN_DEEP_QUERY_COUNT = 8
AUTO_SCAN_MANUAL_QUERY_COUNT = 10
AUTO_SCAN_DEEP_EVERY_BUCKETS = 8
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
        return (
            f"Category: **{category}**\n"
            f"Threshold: **{self.min_discount}%+ verified markdown** • Ranking: **{self.public_mode}**{confidence}{feedback}{memory}{verification}\n"
            f"Checked **{self.products_checked}** products across **{self.searches_checked}** searches.\n"
            f"Verified before memory: **{self.verified_before_memory}** • Verified after memory/ranking: **{self.total_cards}** • Fresh/new/lower-price: **{self.fresh_cards}** • Sent to public guard: **{self.cards_attempted_for_public}**\n"
            f"Posted: **{result.posted}** • Duplicates blocked: **{result.skipped_duplicate}**{duplicate_breakdown} • Not alertable: **{result.skipped_not_alertable}** • Disabled: **{result.skipped_disabled}**\n"
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
        if interaction.guild_id is None:
            await interaction.response.send_message("Use this in a server so I know which auto-scan settings to test.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

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
        asyncio.create_task(self._run_autoscan_now_background(interaction, guild_id, force))

    async def _run_autoscan_now_background(self, interaction: discord.Interaction, guild_id: int, force: bool) -> None:
        lock = autoscan_lock(guild_id)
        async with lock:
            try:
                config = await get_public_alert_config(self.bot.db, guild_id)
                if not config.get("enabled") or not config.get("channel_id"):
                    await self._safe_autoscan_followup(
                        interaction,
                        "Public alerts are not configured yet. Run `/setup_sniperplug_here` first.",
                    )
                    return
                if AUTO_SCAN_RETAILER not in set(config.get("retailers") or ()):
                    await self._safe_autoscan_followup(
                        interaction,
                        "Public alerts are enabled, but Walmart is not in the public retailer list. Run `/setup_sniperplug_here` to repair it.",
                    )
                    return

                report = await self._run_guild_walmart_discovery(
                    AutoScanGuild(guild_id, config.get("channel_id")),
                    force=force,
                )
                await self._send_autoscan_report(interaction, report)
            except Exception as exc:
                log.exception("Manual /autoscan_now failed guild=%s", guild_id)
                await self._safe_autoscan_followup(
                    interaction,
                    f"Auto-scan hit an error after starting: `{clean_log_text(exc)}`",
                )

    async def _send_autoscan_report(self, interaction: discord.Interaction, report: AutoScanReport) -> None:
        embed = discord.Embed(
            title="🧭 Auto-scan test result",
            description=report.discord_summary()[:4000],
            color=discord.Color.green() if report.public_result.posted else discord.Color.orange(),
        )
        if report.review_candidate_summary:
            embed.add_field(
                name="Review-only diagnostics",
                value=trim_discord_value(report.review_candidate_summary),
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
        if report.public_result.errors:
            error_text = chr(10).join(f"• {clean_log_text(error)}" for error in report.public_result.errors[:5])
            embed.add_field(name="Errors", value=error_text, inline=False)
        embed.set_footer(text="This uses the same direct verified auto-scan path as the background loop.")
        await self._safe_autoscan_followup(interaction, embed=embed)

    async def _safe_autoscan_followup(
        self,
        interaction: discord.Interaction,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
    ) -> None:
        try:
            await interaction.followup.send(content=content, embed=embed, ephemeral=True)
        except Exception:
            log.exception("Failed to send /autoscan_now followup")

    @autoscan_now.error
    async def autoscan_now_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = (
            "You need **Manage Server** permission to run an auto-scan test."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Auto-scan test hit an error: `{error}`"
        )
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
            lock = autoscan_lock(guild.guild_id)
            if lock.locked():
                log.info("Auto-scan skipped guild=%s because another auto-scan is already running", guild.guild_id)
                continue
            async with lock:
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
                report = AutoScanReport(guild_id=guild.guild_id, allowed=False, reason=reason, settings=settings)
                await persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
                log.debug("Auto-scan blocked guild=%s retailer=%s reason=%s", guild.guild_id, AUTO_SCAN_RETAILER, reason)
                return report
        else:
            settings = {"forced": True, "retailer": AUTO_SCAN_RETAILER}

        preset = select_autoscan_preset(guild.guild_id, force=force)
        result = await run_autoscan_verified_category(self.bot.db, guild.guild_id, preset=preset)
        warnings = clean_warning_list(result.warnings)
        diagnostics = autoscan_diagnostics(result)

        unique_cards = dedupe_cards(result.cards)
        unique_cards = rank_for_search_mode(unique_cards, [], AUTO_SCAN_PUBLIC_MODE, limit=max(len(unique_cards), AUTO_SCAN_PUBLIC_LIMIT)).verified
        unique_cards = await apply_feedback_learning_to_cards(self.bot.db, guild_id=guild.guild_id, cards=unique_cards, fallback_retailer=AUTO_SCAN_RETAILER)
        feedback_summary = summarize_feedback_learning(unique_cards)
        confidence_selection = select_confident_public_cards(unique_cards, floor=AUTOSCAN_CONFIDENCE_FLOOR)
        fresh_selection = await select_fresh_deal_cards(
            self.bot.db,
            guild_id=guild.guild_id,
            cards=confidence_selection.cards,
            fallback_retailer=AUTO_SCAN_RETAILER,
            limit=AUTO_SCAN_PUBLIC_LIMIT,
            hide_active_cache_repeats=False,
        )

        shown_cards = fresh_selection.fresh
        watchlist_cards: list[DealCard] = []
        if not shown_cards:
            watchlist_cards = prepare_review_watchlist_cards(result, limit=AUTO_SCAN_REVIEW_FALLBACK_LIMIT)
            if watchlist_cards:
                shown_cards = watchlist_cards
                warnings.append("No verified public-confidence cards passed; selected top Walmart Deal Week watchlist/review leads and sent them to the public guard.")

        if not force:
            await record_auto_scan_run(self.bot.db, guild.guild_id, AUTO_SCAN_RETAILER, scan_key=scan_key)

        if not shown_cards:
            report = AutoScanReport(
                guild_id=guild.guild_id,
                allowed=True,
                reason="Auto-scan completed with no new/lower-price verified public-confidence cards.",
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
                route_summary=diagnostics["route_summary"],
                price_memory_summary=diagnostics["price_memory_summary"],
                products_checked=result.products_checked,
                searches_checked=result.searches_attempted,
                total_cards=len(unique_cards),
                verified_before_memory=result.total_verified_cards,
                fresh_cards=0,
                cards_attempted_for_public=0,
                used_repeat_fallback=False,
                repeat_summary=fresh_selection.summary_line(),
                warnings=tuple(warnings),
            )
            await persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
            log.info("Auto-scan completed with no fresh public-confidence cards %s", report.log_fields())
            return report

        public_result = await maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=guild.guild_id,
            cards=shown_cards,
            source_label=f"{AUTO_SCAN_SOURCE_LABEL}:{preset.key}{':watchlist' if watchlist_cards else ''}",
            fallback_retailer=AUTO_SCAN_RETAILER,
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


def select_autoscan_preset(guild_id: int, *, force: bool = False) -> HuntPreset:
    """Rotate categories and query slices so coverage is preserved without hammering Discord/API.

    Each scheduled run scans a small slice of the current category. The slice
    rotates every run, so long query lists still get covered over time. Every
    eighth scheduled bucket runs a wider slice. Manual `/autoscan_now` uses the
    widest slice so staff can debug without waiting for rotation.
    """
    if not AUTO_SCAN_CATEGORY_ROTATION:
        base = HUNT_PRESETS["tech"]
    else:
        bucket = int(time.time() // (AUTO_SCAN_INTERVAL_MINUTES * 60))
        index = (bucket + int(guild_id)) % len(AUTO_SCAN_CATEGORY_ROTATION)
        key = AUTO_SCAN_CATEGORY_ROTATION[index]
        base = HUNT_PRESETS.get(key) or next(iter(HUNT_PRESETS.values()))
    if force:
        query_count = AUTO_SCAN_MANUAL_QUERY_COUNT
    else:
        bucket = int(time.time() // (AUTO_SCAN_INTERVAL_MINUTES * 60))
        query_count = AUTO_SCAN_DEEP_QUERY_COUNT if bucket % AUTO_SCAN_DEEP_EVERY_BUCKETS == 0 else AUTO_SCAN_FAST_QUERY_COUNT
    queries = rotated_query_slice(base.queries, guild_id=guild_id, query_count=query_count)
    return HuntPreset(base.key, base.label, base.emoji, f"{base.description} Auto-scan slice preserves coverage over rotation.", queries, base.min_discount)


def rotated_query_slice(queries: tuple[str, ...], *, guild_id: int, query_count: int) -> tuple[str, ...]:
    if not queries:
        return queries
    count = max(1, min(int(query_count), len(queries)))
    bucket = int(time.time() // (AUTO_SCAN_INTERVAL_MINUTES * 60))
    start = ((bucket + int(guild_id)) * count) % len(queries)
    rotated = [queries[(start + offset) % len(queries)] for offset in range(count)]
    return tuple(rotated)


async def run_autoscan_verified_category(db, guild_id: int, *, preset: HuntPreset) -> VerifiedHuntResult:
    return await collect_verified_discount_cards(
        requested_by="autoscan",
        preset=preset,
        db=db,
        guild_id=guild_id,
        use_price_memory=False,
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
    """Promote the best review/scout cards into a clearly labeled watchlist fallback.

    This is not pretending weak proof is a verified public-confidence deal. It is
    the wake-up lane for sale weeks: if the strict threshold/floor hides
    everything, SniperPlug still surfaces the strongest leads with a big manual
    verification warning.
    """
    review = result.review_candidates
    if review is None or not review.cards:
        return []

    cards: list[DealCard] = []
    for card in review.cards[: max(1, int(limit))]:
        setattr(card, "should_alert", True)
        try:
            card.score = max(int(getattr(card, "score", 0) or 0), 90)
        except Exception:
            setattr(card, "score", 90)
        key = getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or getattr(card, "url", "") or getattr(card, "label", "watchlist")
        price = getattr(card, "current_price", None)
        setattr(card, "public_post_key", f"watchlist:{key}:price:{price}")

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
                        "This is a wake-up candidate, not a blind-buy guarantee. Verify Walmart app price, selected option, seller, and stock before buying/posting."
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


async def list_public_alert_guilds(db) -> list[AutoScanGuild]:
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT guild_id FROM guild_public_alert_settings WHERE enabled = 1 AND channel_id IS NOT NULL"
    )
    rows = await cursor.fetchall()
    guilds: list[AutoScanGuild] = []
    for row in rows:
        guild_id = int(row["guild_id"])
        config = await get_public_alert_config(db, guild_id)
        if AUTO_SCAN_RETAILER in set(config.get("retailers") or ()) and config.get("channel_id"):
            guilds.append(AutoScanGuild(guild_id=guild_id, channel_id=int(config["channel_id"])))
    return guilds


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
