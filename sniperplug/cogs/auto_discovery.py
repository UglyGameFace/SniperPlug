from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.deal_scanner import DealCard, HuntPreset, provider_health_error_message
from sniperplug.cogs.public_alerts import (
    default_auto_scan_config,
    format_daily_limit,
    format_interval,
    list_retailer_auto_scan_settings,
)
from sniperplug.services.autoscan_observed_price_memory import (
    collect_verified_discount_cards_with_observed_memory,
)
from sniperplug.services.deal_category_preferences import (
    apply_category_preferences,
    get_category_preferences,
)
from sniperplug.services.deal_finder_telemetry import top_route_lines
from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.public_deal_posts import PublicPostResult, maybe_post_public_deal_cards
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks
from sniperplug.services.verified_discount_hunt import send_card_batches
from sniperplug.services.walmart_catalog_coverage import (
    catalog_route_pool,
    rotating_catalog_slice,
)
from sniperplug.services.walmart_exact_public_lane import (
    normalize_exact_verified_walmart_cards,
)

DISCORD_EMBED_MESSAGE_LIMIT = 6000
SAFE_EMBED_MESSAGE_LIMIT = 5200
AUTO_DISCOVERY_RETAILER = "walmart"
DISCOVERY_PROGRESS_SECONDS = 45
DISCOVERY_PRIVATE_CARD_LIMIT = 50

DISCOVERY_COVERAGE_CHOICES = [
    app_commands.Choice(name="Quick — 16 rotating routes", value="quick"),
    app_commands.Choice(name="Deep — 64 rotating routes", value="deep"),
    app_commands.Choice(name="Full catalog — every configured route (slow)", value="full"),
]


@dataclass(frozen=True)
class DiscoveryPlan:
    key: str
    label: str
    queries: tuple[str, ...]
    total_routes: int
    slot_index: int
    slot_count: int

    @property
    def estimated_searches(self) -> int:
        # The exact collector currently checks two bounded API pages per route.
        return len(self.queries) * 2

    def coverage_line(self) -> str:
        if self.key == "full":
            return (
                f"**{self.label}** • all **{self.total_routes}** configured routes • "
                f"about **{self.estimated_searches}** bounded API page requests"
            )
        return (
            f"**{self.label}** • **{len(self.queries)}/{self.total_routes}** routes • "
            f"rotation slot **{self.slot_index + 1}/{self.slot_count}** • "
            f"about **{self.estimated_searches}** bounded API page requests"
        )


def resolve_discovery_plan(*, guild_id: int, coverage: str | None = None) -> DiscoveryPlan:
    key = str(coverage or "deep").strip().lower()
    if key not in {"quick", "deep", "full"}:
        key = "deep"

    pool = catalog_route_pool()
    if key == "full":
        return DiscoveryPlan(
            key="full",
            label="Full exact catalog sweep",
            queries=pool,
            total_routes=len(pool),
            slot_index=0,
            slot_count=1,
        )

    route_count = 16 if key == "quick" else 64
    coverage_slice = rotating_catalog_slice(
        guild_id=int(guild_id),
        query_count=route_count,
    )
    return DiscoveryPlan(
        key=key,
        label="Quick exact sweep" if key == "quick" else "Deep exact sweep",
        queries=coverage_slice.queries,
        total_routes=coverage_slice.total_routes,
        slot_index=coverage_slice.slot_index,
        slot_count=coverage_slice.slot_count,
    )


class AutoDiscoveryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="discover",
        description="Run a broad exact-verified Walmart catalog sweep now.",
    )
    @app_commands.describe(
        coverage=(
            "Quick checks 16 rotating routes; Deep checks 64; Full queues every configured "
            "catalog route and can take several minutes."
        ),
        max_public_posts=(
            "Maximum fresh exact-verified deals this manual run may send publicly. "
            "All additional exact cards remain visible in the private result."
        ),
    )
    @app_commands.choices(coverage=DISCOVERY_COVERAGE_CHOICES)
    async def discover(
        self,
        interaction: discord.Interaction,
        coverage: app_commands.Choice[str] | None = None,
        max_public_posts: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        if interaction.guild_id is None:
            await interaction.followup.send(
                "Use `/discover` in a server so SniperPlug can use that server's public-posting, threshold, category, and duplicate settings.",
                ephemeral=True,
            )
            return

        plan = resolve_discovery_plan(
            guild_id=int(interaction.guild_id),
            coverage=coverage.value if coverage else None,
        )
        if plan.key == "full" and not bool(
            getattr(getattr(interaction, "permissions", None), "manage_guild", False)
        ):
            await interaction.followup.send(
                "The full-catalog sweep is owner/staff only because it queues every configured Walmart route. Use `coverage:Deep` for broad normal discovery.",
                ephemeral=True,
            )
            return

        health_error = await provider_health_error_message()
        if health_error:
            await interaction.followup.send(health_error, ephemeral=True)
            return

        # One guild-wide discovery lock prevents different users or coverage
        # options from launching overlapping high-volume Walmart scans.
        lock_key = ScanLockKey(
            guild_id=int(interaction.guild_id),
            user_id=0,
            action="manual_exact_discovery",
            preset="catalog_wide_exact",
        )
        if not await scan_operation_locks.acquire(lock_key):
            await interaction.followup.send(
                "A catalog discovery sweep is already running in this server. I blocked the overlapping run so Walmart is not hammered and Discord is not double-posted.",
                ephemeral=True,
            )
            return

        started = time.monotonic()
        progress_task: asyncio.Task | None = None
        try:
            await interaction.followup.send(
                "⏳ Starting Walmart discovery. "
                + plan.coverage_line()
                + "\nSearch responses only discover item IDs; a card still needs official exact-item price, seller/offer identity, and trusted markdown proof.",
                ephemeral=True,
            )
            progress_task = asyncio.create_task(
                self._discovery_progress_notice(interaction, plan=plan, started=started),
                name=f"sniperplug-discover-progress-{interaction.guild_id}",
            )

            auto_scan_settings = await list_retailer_auto_scan_settings(
                self.bot.db,
                int(interaction.guild_id),
            )
            gate_settings = auto_scan_settings.get(
                AUTO_DISCOVERY_RETAILER,
                default_auto_scan_config(AUTO_DISCOVERY_RETAILER),
            )
            preset = HuntPreset(
                key=f"discover_{plan.key}",
                label=plan.label,
                emoji="🌐",
                description=(
                    "Manual catalog-wide discovery with official exact-detail verification and "
                    "global queue retention."
                ),
                queries=plan.queries,
                min_discount=50,
            )
            result = await collect_verified_discount_cards_with_observed_memory(
                requested_by=f"discover:{interaction.user.id}",
                preset=preset,
                db=self.bot.db,
                guild_id=int(interaction.guild_id),
                use_price_memory=True,
            )

            exact_cards = list(result.cards)
            normalized_exact = normalize_exact_verified_walmart_cards(
                exact_cards,
                min_discount=result.min_discount,
            )
            fresh_selection = await select_fresh_deal_cards(
                self.bot.db,
                guild_id=int(interaction.guild_id),
                cards=exact_cards,
                fallback_retailer=AUTO_DISCOVERY_RETAILER,
                limit=max(len(exact_cards), 1),
                hide_active_cache_repeats=False,
                min_public_discount=result.min_discount,
                source_label=f"discover:{plan.key}:exact_verified",
            )
            shown_cards = list(fresh_selection.fresh)
            category_preferences = await get_category_preferences(
                self.bot.db,
                int(interaction.guild_id),
            )
            shown_cards, category_suppressed_cards, category_notes = apply_category_preferences(
                shown_cards,
                category_preferences,
            )

            public_cards = shown_cards[: max(1, int(max_public_posts))]
            normalize_exact_verified_walmart_cards(
                public_cards,
                min_discount=result.min_discount,
            )
            public_result = await maybe_post_public_deal_cards(
                bot=self.bot,
                guild_id=int(interaction.guild_id),
                cards=public_cards,
                source_label=f"discover:{plan.key}:exact_verified_{result.min_discount}_plus",
                fallback_retailer=AUTO_DISCOVERY_RETAILER,
                min_public_discount=result.min_discount,
            )

            review_count = len(result.review_candidates.cards) if result.review_candidates else 0
            elapsed = max(0, int(time.monotonic() - started))
            embed = discord.Embed(
                title="🌐 Exact-Verified Walmart Discovery",
                description=(
                    "`/discover` is now the broad manual sweep. `/autoscan_now` remains the smaller "
                    "diagnostic/rotation test.\n\n"
                    f"Coverage: {plan.coverage_line()}\n"
                    f"Threshold: **{result.min_discount}%+ verified markdown**\n"
                    f"Checked: **{result.products_checked} returned products** across "
                    f"**{result.pages_checked} API result pages**\n"
                    f"Exact verified total: **{result.total_verified_cards}** • "
                    f"fresh/private results: **{len(shown_cards)}**\n"
                    f"Public cap for this run: **{int(max_public_posts)}** • "
                    f"sent to public guard: **{len(public_cards)}**\n"
                    f"Review/under-threshold exact leads: **{review_count}** • elapsed: **{elapsed}s**\n"
                    f"Fresh filter: {fresh_selection.summary_line()}"
                ),
                color=discord.Color.green() if public_result.posted else discord.Color.dark_gold(),
            )
            embed.add_field(
                name="Auto-scan setting",
                value=discover_auto_scan_status(gate_settings),
                inline=False,
            )
            route_lines = top_route_lines(result.route_stats, limit=5)
            if route_lines:
                embed.add_field(
                    name="🧭 Productive routes",
                    value="\n".join(route_lines)[:1024],
                    inline=False,
                )
            if category_notes or category_suppressed_cards:
                lines = []
                if category_suppressed_cards:
                    lines.append(
                        f"Muted category settings hid **{len(category_suppressed_cards)}** normal public lead(s). Extreme/nuclear deals still break through."
                    )
                lines.extend(f"• {note}" for note in category_notes[:3])
                embed.add_field(
                    name="🎛️ Deal Feed Controls",
                    value="\n".join(lines)[:1024],
                    inline=False,
                )
            if len(shown_cards) > len(public_cards):
                embed.add_field(
                    name="More exact deals found",
                    value=(
                        f"**{len(shown_cards) - len(public_cards)}** additional fresh exact-verified card(s) were kept in the private results instead of flooding the public channel."
                    ),
                    inline=False,
                )
            if result.price_memory is not None:
                embed.add_field(
                    name="🧠 Price memory",
                    value=result.price_memory.summary_line(),
                    inline=False,
                )
            if result.review_candidates is not None:
                embed.add_field(
                    name="🟨 Exact review / under-threshold audit",
                    value=result.review_candidates.summary_line(),
                    inline=False,
                )
            if normalized_exact:
                embed.add_field(
                    name="✅ Exact-detail normalization",
                    value=(
                        f"Refreshed **{normalized_exact}** exact Walmart card(s) immediately before public gating. Search-only rows cannot become deal cards."
                    ),
                    inline=False,
                )
            if public_result.any_activity:
                embed.add_field(
                    name="📣 Public posting",
                    value=public_posting_summary(public_result),
                    inline=False,
                )
            useful_notes = select_discovery_notes(result.warnings)
            if useful_notes:
                embed.add_field(
                    name="ℹ️ Exact queue / coverage notes",
                    value="\n".join(f"• {note}" for note in useful_notes)[:1024],
                    inline=False,
                )
            embed.set_footer(
                text=(
                    "Search finds item IDs; only official exact-detail seller/offer and price proof can create a deal card. "
                    "Use /deal_threshold to include smaller verified markdowns."
                )
            )

            review_cards = (
                list(result.review_candidates.cards[:10])
                if result.review_candidates
                else []
            )
            private_cards = shown_cards[:DISCOVERY_PRIVATE_CARD_LIMIT]
            if len(shown_cards) > DISCOVERY_PRIVATE_CARD_LIMIT:
                embed.add_field(
                    name="Private display cap",
                    value=(
                        f"Showing the top **{DISCOVERY_PRIVATE_CARD_LIMIT}** exact cards here; all discovered item IDs remain retained by the global exact-detail queue."
                    ),
                    inline=False,
                )

            if not private_cards:
                await interaction.followup.send(embed=embed, ephemeral=True)
                if review_cards:
                    await send_card_batches(
                        interaction,
                        summary=discord.Embed(
                            title="🟨 Exact review / under-threshold leads",
                            description=(
                                "These were exact-detail checked but did not qualify for automatic public posting at the current threshold."
                            ),
                            color=discord.Color.gold(),
                        ),
                        cards=[],
                        review_cards=review_cards,
                    )
                return

            await send_card_batches(
                interaction,
                summary=embed,
                cards=private_cards,
                review_cards=review_cards,
            )
        finally:
            if progress_task is not None:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
            await scan_operation_locks.release(lock_key)

    async def _discovery_progress_notice(
        self,
        interaction: discord.Interaction,
        *,
        plan: DiscoveryPlan,
        started: float,
    ) -> None:
        notice = 0
        try:
            while True:
                await asyncio.sleep(DISCOVERY_PROGRESS_SECONDS)
                notice += 1
                elapsed = max(1, int(time.monotonic() - started))
                await interaction.followup.send(
                    "⏳ Exact Walmart discovery is still active. "
                    f"Coverage: **{plan.label}**, routes: **{len(plan.queries)}**, elapsed: **{elapsed}s**. "
                    "Completed search results are being retained in the global exact-detail queue. "
                    f"Progress update #{notice}.",
                    ephemeral=True,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            return

    @discover.error
    async def discover_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = f"Discovery hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def select_discovery_notes(warnings: list[str] | tuple[str, ...], *, limit: int = 5) -> list[str]:
    priority_markers = (
        "exact-detail queue",
        "global exact-detail queue",
        "official walmart detail gate",
        "exact walmart detail checks",
        "catalog-wide route rotation",
    )
    hidden_markers = (
        "walmart_publisher_id",
        "lightweight scan",
    )
    selected: list[str] = []
    deferred: list[str] = []
    for warning in warnings:
        clean = " ".join(str(warning or "").split())
        if not clean or any(marker in clean.lower() for marker in hidden_markers):
            continue
        if any(marker in clean.lower() for marker in priority_markers):
            if clean not in selected:
                selected.append(clean)
        elif clean not in deferred:
            deferred.append(clean)
    for item in deferred:
        if len(selected) >= max(1, int(limit)):
            break
        selected.append(item)
    return selected[: max(1, int(limit))]


def public_posting_summary(result: PublicPostResult) -> str:
    lines = [
        f"Posted: **{result.posted}**",
        f"Duplicate blocked: **{result.skipped_duplicate}**",
        f"Not alertable/private review: **{result.skipped_not_alertable}**",
        f"Wrong retailer blocked: **{getattr(result, 'skipped_wrong_retailer', 0)}**",
        f"Disabled/config blocked: **{getattr(result, 'skipped_disabled', 0)}**",
        f"Cached active: **{getattr(result, 'cached_active', 0)}**",
    ]
    if result.errors:
        lines.append("Errors:\n" + "\n".join(f"• {error}" for error in result.errors[:4]))
    return "\n".join(lines)


def embed_text_size(embed: discord.Embed) -> int:
    total = 0
    if embed.title:
        total += len(str(embed.title))
    if embed.description:
        total += len(str(embed.description))
    for field in embed.fields:
        total += len(str(field.name)) + len(str(field.value))
    footer = getattr(embed, "footer", None)
    footer_text = getattr(footer, "text", None)
    if footer_text:
        total += len(str(footer_text))
    author = getattr(embed, "author", None)
    author_name = getattr(author, "name", None)
    if author_name:
        total += len(str(author_name))
    return total


def batch_cards_for_embed_limit(
    cards: list[DealCard],
    *,
    limit: int = SAFE_EMBED_MESSAGE_LIMIT,
) -> list[list[DealCard]]:
    batches: list[list[DealCard]] = []
    current: list[DealCard] = []
    current_size = 0
    for card in cards:
        size = embed_text_size(card.embed)
        if current and current_size + size > limit:
            batches.append(current)
            current = []
            current_size = 0
        current.append(card)
        current_size += size
    if current:
        batches.append(current)
    return batches


def manual_discover_note(settings: dict) -> str:
    if not settings.get("enabled"):
        return (
            f"Manual `/discover` override: `{AUTO_DISCOVERY_RETAILER}` auto-scan is off, but this manual command is allowed."
        )
    return (
        f"Manual `/discover` run. `{AUTO_DISCOVERY_RETAILER}` auto-scan settings only gate scheduled/background pulls."
    )


def discover_auto_scan_status(settings: dict) -> str:
    interval_hours = int(
        settings.get("interval_hours")
        if settings.get("interval_hours") is not None
        else 6
    )
    daily_limit = int(
        settings.get("daily_limit")
        if settings.get("daily_limit") is not None
        else 25
    )
    return (
        f"Retailer: `{AUTO_DISCOVERY_RETAILER}`\n"
        f"Auto enabled: **{'yes' if settings.get('enabled') else 'no'}**\n"
        f"Interval: **{format_interval(interval_hours)}**\n"
        f"Daily limit: **{format_daily_limit(daily_limit)}**\n"
        "Manual `/discover`: **allowed**"
    )
