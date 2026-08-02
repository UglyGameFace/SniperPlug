from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs import auto_scan_runner as autoscan_runtime
from sniperplug.cogs.deal_scanner import DealCard, HuntPreset, provider_health_error_message
from sniperplug.cogs.public_alerts import (
    default_auto_scan_config,
    format_daily_limit,
    format_interval,
    list_retailer_auto_scan_settings,
)
from sniperplug.cogs.resilient_auto_scan_runner import _WALMART_PROVIDER_OPERATION_LOCK
from sniperplug.services import verified_discount_hunt as hunt
from sniperplug.services.autoscan_observed_price_memory import (
    collect_verified_discount_cards_with_observed_memory,
)
from sniperplug.services.deal_category_preferences import (
    apply_category_preferences,
    get_category_preferences,
)
from sniperplug.services.deal_finder_telemetry import merge_route_stats, top_route_lines
from sniperplug.services.deal_ranking import rank_verified_cards
from sniperplug.services.embed_delivery import batch_cards_for_limit, sanitize_embed
from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.public_deal_posts import PublicPostResult, maybe_post_public_deal_cards
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks
from sniperplug.services.walmart_catalog_coverage import (
    catalog_route_pool,
    rotating_catalog_slice,
)
from sniperplug.services.walmart_exact_public_lane import (
    normalize_exact_verified_walmart_cards,
)

log = logging.getLogger("sniperplug.discovery")

DISCORD_EMBED_MESSAGE_LIMIT = 6000
SAFE_EMBED_MESSAGE_LIMIT = 5200
AUTO_DISCOVERY_RETAILER = "walmart"
DISCOVERY_PROGRESS_SECONDS = 45
DISCOVERY_PRIVATE_CARD_LIMIT = 50
DISCOVERY_CHUNK_ROUTES = 16
DISCOVERY_MAX_RUNTIME_SECONDS = 2 * 60 * 60

DISCOVERY_COVERAGE_CHOICES = [
    app_commands.Choice(name="Quick — 16 rotating routes", value="quick"),
    app_commands.Choice(name="Deep — 64 rotating routes", value="deep"),
    app_commands.Choice(name="Full catalog — every configured route (background)", value="full"),
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
        # The exact collector checks two bounded API pages per route.
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


@dataclass
class DiscoveryJob:
    job_id: str
    guild_id: int
    user_id: int
    plan: DiscoveryPlan
    max_public_posts: int
    lock_key: ScanLockKey
    guild_scan_lock: asyncio.Lock
    started_monotonic: float
    status_message: discord.Message | None = None
    view: discord.ui.View | None = None
    delivery_kind: str = "dm"
    task: asyncio.Task | None = None
    state: str = "starting"
    current_chunk: int = 0
    total_chunks: int = 0
    routes_completed: int = 0
    pages_checked: int = 0
    products_checked: int = 0
    exact_cards: int = 0
    review_count: int = 0
    public_posted: int = 0
    error: str = ""

    @property
    def elapsed_seconds(self) -> int:
        return max(0, int(time.monotonic() - self.started_monotonic))

    @property
    def active(self) -> bool:
        return self.task is None or not self.task.done()


class DiscoveryJobView(discord.ui.View):
    def __init__(self, cog: "AutoDiscoveryCog", guild_id: int):
        super().__init__(timeout=DISCOVERY_MAX_RUNTIME_SECONDS)
        self.cog = cog
        self.guild_id = int(guild_id)

    @discord.ui.button(label="Refresh status", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_status(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        job = self.cog._active_jobs.get(self.guild_id)
        if job is None:
            await interaction.response.send_message(
                "That discovery job has already finished or the bot restarted.",
                ephemeral=True,
            )
            return
        await interaction.response.edit_message(
            embed=self.cog._build_job_status_embed(job),
            view=self,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel_job(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        job = self.cog._active_jobs.get(self.guild_id)
        if job is None:
            await interaction.response.send_message(
                "That discovery job is no longer active.",
                ephemeral=True,
            )
            return
        if not self.cog._can_control_job(interaction.user.id, job):
            await interaction.response.send_message(
                "Only the person who started this job or a server manager can cancel it.",
                ephemeral=True,
            )
            return
        job.state = "cancel_requested"
        if job.task is not None and not job.task.done():
            job.task.cancel()
        await interaction.response.edit_message(
            embed=self.cog._build_job_status_embed(job),
            view=self,
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


def chunk_discovery_queries(
    queries: tuple[str, ...],
    *,
    chunk_size: int = DISCOVERY_CHUNK_ROUTES,
) -> tuple[tuple[str, ...], ...]:
    safe_size = max(1, int(chunk_size))
    return tuple(
        tuple(queries[index : index + safe_size])
        for index in range(0, len(queries), safe_size)
    )


class AutoDiscoveryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active_jobs: dict[int, DiscoveryJob] = {}

    def cog_unload(self) -> None:
        for job in tuple(self._active_jobs.values()):
            if job.task is not None and not job.task.done():
                job.task.cancel()

    @app_commands.command(
        name="discover",
        description="Start a broad exact-verified Walmart catalog discovery job.",
    )
    @app_commands.describe(
        coverage="Quick: 16 routes. Deep: 64. Full: every route in a durable background job.",
        max_public_posts="Fresh verified deals sent publicly; extra exact cards are delivered privately.",
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
                "Use `/discover` in a server so SniperPlug can use that server's threshold, category, channel, and duplicate settings.",
                ephemeral=True,
            )
            return

        guild_id = int(interaction.guild_id)
        plan = resolve_discovery_plan(
            guild_id=guild_id,
            coverage=coverage.value if coverage else None,
        )
        if plan.key == "full" and not bool(
            getattr(getattr(interaction, "permissions", None), "manage_guild", False)
        ):
            await interaction.followup.send(
                "The full-catalog sweep is owner/staff only. Use `coverage:Deep` for broad normal discovery.",
                ephemeral=True,
            )
            return

        health_error = await provider_health_error_message()
        if health_error:
            await interaction.followup.send(health_error, ephemeral=True)
            return

        existing = self._active_jobs.get(guild_id)
        if existing is not None and existing.active:
            await interaction.followup.send(
                embed=self._build_job_status_embed(existing),
                ephemeral=True,
            )
            return

        lock_key = ScanLockKey(
            guild_id=guild_id,
            user_id=0,
            action="manual_exact_discovery",
            preset="catalog_wide_exact",
        )
        if not await scan_operation_locks.acquire(lock_key):
            await interaction.followup.send(
                "A Walmart discovery job is already running in this server. The overlap was blocked so requests and public posts cannot duplicate.",
                ephemeral=True,
            )
            return

        guild_scan_lock = autoscan_runtime.autoscan_lock(guild_id)
        if guild_scan_lock.locked():
            await scan_operation_locks.release(lock_key)
            await interaction.followup.send(
                "A Walmart autoscan or discovery job is already running for this server. Try again after that job finishes.",
                ephemeral=True,
            )
            return
        await guild_scan_lock.acquire()

        job = DiscoveryJob(
            job_id=uuid.uuid4().hex[:8].upper(),
            guild_id=guild_id,
            user_id=int(interaction.user.id),
            plan=plan,
            max_public_posts=max(1, int(max_public_posts)),
            lock_key=lock_key,
            guild_scan_lock=guild_scan_lock,
            started_monotonic=time.monotonic(),
        )
        job.total_chunks = len(chunk_discovery_queries(plan.queries))
        self._active_jobs[guild_id] = job
        view = DiscoveryJobView(self, guild_id)
        job.view = view

        try:
            job.status_message, job.delivery_kind = await self._create_status_message(
                interaction,
                job,
                view,
            )
            job.task = asyncio.create_task(
                self._run_discovery_job(job),
                name=f"sniperplug-discovery-{guild_id}-{job.job_id}",
            )
        except Exception:
            self._active_jobs.pop(guild_id, None)
            if guild_scan_lock.locked():
                guild_scan_lock.release()
            await scan_operation_locks.release(lock_key)
            log.exception("Failed to start Walmart discovery job guild=%s", guild_id)
            await interaction.followup.send(
                "I could not create a durable status message, so the discovery job was not started. Check my channel permissions or allow DMs and try again.",
                ephemeral=True,
            )
            return

        destination = "your DMs" if job.delivery_kind == "dm" else "one editable status message in this channel"
        await interaction.followup.send(
            f"✅ Walmart discovery job `{job.job_id}` started. Progress and the final result will use {destination}; it will not stack a new message every 45 seconds.",
            ephemeral=True,
        )

    async def _create_status_message(
        self,
        interaction: discord.Interaction,
        job: DiscoveryJob,
        view: DiscoveryJobView,
    ) -> tuple[discord.Message, str]:
        embed = self._build_job_status_embed(job)
        try:
            message = await interaction.user.send(embed=embed, view=view)
            return message, "dm"
        except (discord.Forbidden, discord.HTTPException):
            channel = interaction.channel
            if channel is None or not hasattr(channel, "send"):
                raise
            message = await channel.send(
                content=f"<@{job.user_id}> Walmart discovery status",
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            return message, "channel"

    async def _run_discovery_job(self, job: DiscoveryJob) -> None:
        heartbeat = asyncio.create_task(
            self._job_heartbeat(job),
            name=f"sniperplug-discovery-heartbeat-{job.guild_id}-{job.job_id}",
        )
        try:
            job.state = "searching"
            self._set_watchdog_phase(
                f"discover_{job.plan.key}:guild={job.guild_id}:chunk=1/{max(1, job.total_chunks)}"
            )
            await self._safe_edit_job_message(job)
            async with asyncio.timeout(DISCOVERY_MAX_RUNTIME_SECONDS):
                async with _WALMART_PROVIDER_OPERATION_LOCK:
                    result = await self._collect_chunked_discovery(job)
            job.state = "posting"
            self._set_watchdog_phase(f"discover_posting:guild={job.guild_id}")
            await self._safe_edit_job_message(job)
            await self._finish_discovery_job(job, result)
            job.state = "complete"
        except asyncio.CancelledError:
            job.state = "cancelled"
            job.error = (
                "Cancelled. Every completed route chunk was already written to the global exact-detail queue; only the in-progress chunk may need to be rediscovered."
            )
            await self._safe_edit_job_message(job, final=True)
        except TimeoutError:
            job.state = "timed_out"
            job.error = (
                "The two-hour safety limit stopped this job. Completed chunks remain in the exact-detail queue and the background verifier can continue them."
            )
            await self._safe_edit_job_message(job, final=True)
        except Exception as error:  # noqa: BLE001 - durable job must fail visibly and release locks.
            job.state = "failed"
            job.error = f"{type(error).__name__}: {error}"
            log.exception(
                "Walmart discovery job failed guild=%s job=%s",
                job.guild_id,
                job.job_id,
            )
            await self._safe_edit_job_message(job, final=True)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            self._set_watchdog_phase("idle")
            if job.guild_scan_lock.locked():
                job.guild_scan_lock.release()
            await scan_operation_locks.release(job.lock_key)
            self._active_jobs.pop(job.guild_id, None)

    async def _collect_chunked_discovery(self, job: DiscoveryJob) -> hunt.VerifiedHuntResult:
        chunks = chunk_discovery_queries(job.plan.queries)
        all_cards: list[DealCard] = []
        warnings: list[str] = []
        route_stats = []
        pages_checked = 0
        products_checked = 0
        searches_attempted = 0
        review_count = 0
        min_discount: int | None = None

        for index, queries in enumerate(chunks, start=1):
            job.current_chunk = index
            self._set_watchdog_phase(
                f"discover_{job.plan.key}:guild={job.guild_id}:chunk={index}/{len(chunks)}"
            )
            await self._safe_edit_job_message(job)
            preset = HuntPreset(
                key=f"discover_{job.plan.key}_{index}",
                label=f"{job.plan.label} chunk {index}/{len(chunks)}",
                emoji="🌐",
                description="Chunked catalog discovery with exact-detail verification and queue checkpointing.",
                queries=queries,
                min_discount=min_discount or 50,
            )
            chunk_result = await collect_verified_discount_cards_with_observed_memory(
                requested_by=f"discover:{job.user_id}:{job.job_id}:chunk:{index}",
                preset=preset,
                db=self.bot.db,
                guild_id=job.guild_id,
                use_price_memory=False,
                min_discount=min_discount,
            )
            min_discount = int(chunk_result.min_discount)
            all_cards.extend(chunk_result.cards)
            pages_checked += int(chunk_result.pages_checked)
            products_checked += int(chunk_result.products_checked)
            searches_attempted += int(chunk_result.searches_attempted)
            route_stats.extend(chunk_result.route_stats)
            review_count += (
                len(chunk_result.review_candidates.cards)
                if chunk_result.review_candidates is not None
                else 0
            )
            for warning in chunk_result.warnings:
                if warning not in warnings:
                    warnings.append(warning)

            job.routes_completed += len(queries)
            job.pages_checked = pages_checked
            job.products_checked = products_checked
            job.exact_cards = len(hunt.dedupe_cards(all_cards))
            job.review_count = review_count
            await self._safe_edit_job_message(job)
            await asyncio.sleep(0)

        ranked_cards = rank_verified_cards(hunt.dedupe_cards(all_cards))
        return hunt.VerifiedHuntResult(
            cards=ranked_cards,
            pages_checked=pages_checked,
            products_checked=products_checked,
            warnings=warnings,
            searches_attempted=searches_attempted,
            min_discount=min_discount or 50,
            price_memory=None,
            total_verified_cards=len(ranked_cards),
            review_candidates=None,
            category_key=f"discover_{job.plan.key}",
            route_stats=merge_route_stats(route_stats),
            scout_lead_count=0,
            memory_recheck_count=0,
        )

    async def _finish_discovery_job(
        self,
        job: DiscoveryJob,
        result: hunt.VerifiedHuntResult,
    ) -> None:
        auto_scan_settings = await list_retailer_auto_scan_settings(
            self.bot.db,
            job.guild_id,
        )
        gate_settings = auto_scan_settings.get(
            AUTO_DISCOVERY_RETAILER,
            default_auto_scan_config(AUTO_DISCOVERY_RETAILER),
        )

        exact_cards = list(result.cards)
        normalized_exact = normalize_exact_verified_walmart_cards(
            exact_cards,
            min_discount=result.min_discount,
        )
        category_preferences = await get_category_preferences(
            self.bot.db,
            job.guild_id,
        )
        shown_cards, category_suppressed_cards, category_notes = apply_category_preferences(
            exact_cards,
            category_preferences,
        )
        fresh_selection = await select_fresh_deal_cards(
            self.bot.db,
            guild_id=job.guild_id,
            cards=shown_cards,
            fallback_retailer=AUTO_DISCOVERY_RETAILER,
            limit=max(len(shown_cards), 1),
            hide_active_cache_repeats=False,
            min_public_discount=result.min_discount,
            source_label=f"discover:{job.plan.key}:exact_verified",
        )
        fresh_cards = list(fresh_selection.fresh)
        public_cards = fresh_cards[: job.max_public_posts]
        normalize_exact_verified_walmart_cards(
            public_cards,
            min_discount=result.min_discount,
        )
        public_result = await maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=job.guild_id,
            cards=public_cards,
            source_label=f"discover:{job.plan.key}:exact_verified_{result.min_discount}_plus",
            fallback_retailer=AUTO_DISCOVERY_RETAILER,
            min_public_discount=result.min_discount,
        )
        job.public_posted = int(public_result.posted)
        job.exact_cards = len(shown_cards)

        private_cards = shown_cards[:DISCOVERY_PRIVATE_CARD_LIMIT]
        summary = self._build_result_embed(
            job=job,
            result=result,
            gate_settings=gate_settings,
            normalized_exact=normalized_exact,
            category_suppressed_cards=category_suppressed_cards,
            category_notes=category_notes,
            shown_cards=shown_cards,
            fresh_cards=fresh_cards,
            public_cards=public_cards,
            public_result=public_result,
            fresh_summary=fresh_selection.summary_line(),
        )

        if job.status_message is not None:
            await job.status_message.edit(embed=sanitize_embed(summary), view=None)

        if job.delivery_kind == "dm" and private_cards:
            delivered = 0
            for batch in batch_cards_for_limit(private_cards):
                await job.status_message.channel.send(
                    embeds=[sanitize_embed(card.embed) for card in batch],
                )
                delivered += len(batch)
            log.info(
                "Discovery private exact cards delivered guild=%s job=%s cards=%s",
                job.guild_id,
                job.job_id,
                delivered,
            )

    def _build_result_embed(
        self,
        *,
        job: DiscoveryJob,
        result: hunt.VerifiedHuntResult,
        gate_settings: dict,
        normalized_exact: int,
        category_suppressed_cards: list[DealCard],
        category_notes: list[str],
        shown_cards: list[DealCard],
        fresh_cards: list[DealCard],
        public_cards: list[DealCard],
        public_result: PublicPostResult,
        fresh_summary: str,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="✅ Exact-Verified Walmart Discovery Complete",
            description=(
                f"Job: `{job.job_id}`\n"
                f"Coverage: {job.plan.coverage_line()}\n"
                f"Threshold: **{result.min_discount}%+ verified markdown**\n"
                f"Completed routes: **{job.routes_completed}/{len(job.plan.queries)}** in "
                f"**{job.total_chunks}** queue-checkpointed chunk(s)\n"
                f"Checked: **{result.products_checked} returned products** across "
                f"**{result.pages_checked} API result pages**\n"
                f"Exact verified total: **{result.total_verified_cards}** • "
                f"private exact results: **{len(shown_cards)}** • "
                f"fresh public-ready: **{len(fresh_cards)}**\n"
                f"Public cap: **{job.max_public_posts}** • sent to public guard: **{len(public_cards)}** • "
                f"posted: **{public_result.posted}**\n"
                f"Exact review/under-threshold audit count: **{job.review_count}** • "
                f"elapsed: **{job.elapsed_seconds}s**\n"
                f"Fresh filter: {fresh_summary}"
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
                    f"**{len(shown_cards) - len(public_cards)}** additional exact-verified card(s), including already-posted duplicates when present, were kept out of the public flood."
                ),
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
        if len(shown_cards) > DISCOVERY_PRIVATE_CARD_LIMIT:
            embed.add_field(
                name="Private display cap",
                value=(
                    f"Showing the top **{DISCOVERY_PRIVATE_CARD_LIMIT}** exact cards in DM; every discovered item ID remains in the global exact-detail queue."
                ),
                inline=False,
            )
        if job.delivery_kind != "dm":
            embed.add_field(
                name="Private delivery fallback",
                value=(
                    "Your DMs were closed, so this single channel message was used for durable status. Exact cards were not dumped into the channel; fresh public-safe deals still used the configured public posting lane."
                ),
                inline=False,
            )
        embed.set_footer(
            text=(
                "Search finds item IDs; only official exact-detail seller/offer and price proof can create a deal card. "
                "Use /deal_threshold to include smaller verified markdowns."
            )
        )
        return embed

    async def _job_heartbeat(self, job: DiscoveryJob) -> None:
        try:
            while True:
                await asyncio.sleep(DISCOVERY_PROGRESS_SECONDS)
                await self._safe_edit_job_message(job)
        except asyncio.CancelledError:
            return

    async def _safe_edit_job_message(self, job: DiscoveryJob, *, final: bool = False) -> None:
        if job.status_message is None:
            return
        try:
            await job.status_message.edit(
                embed=sanitize_embed(self._build_job_status_embed(job)),
                view=None if final else job.view,
            )
        except (discord.NotFound, discord.Forbidden):
            return
        except discord.HTTPException:
            log.warning(
                "Discovery status message update failed guild=%s job=%s",
                job.guild_id,
                job.job_id,
            )

    def _build_job_status_embed(self, job: DiscoveryJob) -> discord.Embed:
        total_routes = max(1, len(job.plan.queries))
        completed = min(job.routes_completed, total_routes)
        percent = (completed / total_routes) * 100
        remaining = max(0, total_routes - completed)
        eta = "calculating"
        if completed > 0 and remaining > 0:
            seconds_per_route = job.elapsed_seconds / completed
            eta_seconds = max(1, int(seconds_per_route * remaining))
            eta = format_duration(eta_seconds)
        elif remaining == 0:
            eta = "finishing public/private delivery"

        state_labels = {
            "starting": "Starting",
            "searching": "Searching and checkpointing",
            "posting": "Applying final proof, duplicate, and public-post gates",
            "cancel_requested": "Cancellation requested",
            "cancelled": "Cancelled",
            "timed_out": "Stopped by safety timeout",
            "failed": "Failed safely",
            "complete": "Complete",
        }
        color = discord.Color.blurple()
        if job.state in {"cancelled", "timed_out", "failed"}:
            color = discord.Color.orange()
        elif job.state == "complete":
            color = discord.Color.green()

        chunk_line = "Preparing first chunk"
        if job.total_chunks:
            active_chunk = min(max(job.current_chunk, 1), job.total_chunks)
            start_route = (active_chunk - 1) * DISCOVERY_CHUNK_ROUTES + 1
            end_route = min(active_chunk * DISCOVERY_CHUNK_ROUTES, total_routes)
            chunk_line = (
                f"Chunk **{active_chunk}/{job.total_chunks}** • routes **{start_route}-{end_route}**"
            )

        description = (
            f"Job: `{job.job_id}` • State: **{state_labels.get(job.state, job.state)}**\n"
            f"Coverage: {job.plan.coverage_line()}\n"
            f"{chunk_line}\n"
            f"Actual progress: **{completed}/{total_routes} routes ({percent:.1f}%)**\n"
            f"Completed API pages: **{job.pages_checked}** • returned products: **{job.products_checked}**\n"
            f"Exact cards accumulated: **{job.exact_cards}** • audit-only exact leads: **{job.review_count}**\n"
            f"Elapsed: **{format_duration(job.elapsed_seconds)}** • ETA: **{eta}**\n\n"
            "Each completed chunk is already retained in the global exact-detail queue. The in-progress chunk is the only work that could be lost if the bot restarts or the job is cancelled."
        )
        if job.error:
            description += f"\n\nResult: {job.error}"

        embed = discord.Embed(
            title="🌐 Walmart Discovery Job",
            description=description,
            color=color,
        )
        embed.set_footer(
            text="One status message is edited in place. Public posts still require exact price, seller/offer identity, threshold, availability, freshness, and duplicate proof."
        )
        return embed

    def _can_control_job(self, user_id: int, job: DiscoveryJob) -> bool:
        if int(user_id) == int(job.user_id):
            return True
        guild = self.bot.get_guild(job.guild_id)
        if guild is None:
            return False
        member = guild.get_member(int(user_id))
        return bool(member and member.guild_permissions.manage_guild)

    def _set_watchdog_phase(self, phase: str) -> None:
        # The autoscan watchdog lives in another cog. Updating its runtime phase
        # prevents a long /discover job from being mislabeled as phase=idle.
        for cog in tuple(getattr(self.bot, "cogs", {}).values()):
            if hasattr(cog, "_event_loop_watchdog_task") and hasattr(cog, "_runtime_phase"):
                setattr(cog, "_runtime_phase", str(phase or "idle"))

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


def format_duration(seconds: int | float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


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
