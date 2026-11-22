from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.embed_delivery import send_summary_and_embeds
from sniperplug.services.hd_penny_stages import PennyStage
from sniperplug.services.hd_penny_tracker import (
    HdPennyStatus,
    load_hd_penny_status,
    seed_from_hunt_candidates,
)
from sniperplug.services.hd_standing_hunts import (
    list_standing_hunts,
    register_standing_hunt,
    stop_standing_hunt,
)
from sniperplug.services.hd_user_location import (
    HdUserLocation,
    get_hd_user_location,
    normalize_hd_store_id,
    normalize_hd_zip,
    save_hd_user_location,
)
from sniperplug.services.penny_score import score_penny_candidate
from sniperplug.services.quota_guard import serpapi_quota_guard
from sniperplug.services.safe_links import product_link_choices
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks


BROAD_QUERY_TERMS = {"clearance", "sale", "tools", "home depot", "deal", "deals"}
BROAD_QUERY_EXAMPLES = "`milwaukee drill`, `faucet`, `vanity`, `ryobi battery`, `ceiling fan`, or `shower head`"


@dataclass(frozen=True)
class HomeDepotCardBatch:
    embeds: list[discord.Embed]
    candidates: list[SourceCandidate]
    returned_count: int
    shown_count: int
    filtered_count: int
    used_raw_fallback: bool


class HomeDepotSearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="home_depot_search", description="Manually search Home Depot through SerpApi with quota protection.")
    @app_commands.describe(
        query="Product search, like milwaukee drill, vanity, faucet, ceiling fan.",
        store_id="Optional Home Depot store ID. Leave blank to use your saved store.",
        zip_code="Optional delivery/local ZIP. Leave blank to use your saved Home Depot ZIP.",
        page="Page to scan. Page 2+ uses another SerpApi credit.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def home_depot_search(
        self,
        interaction: discord.Interaction,
        query: str,
        store_id: str | None = None,
        zip_code: str | None = None,
        page: app_commands.Range[int, 1, 5] = 1,
    ) -> None:
        await self._run_search(interaction, query=query, store_id=store_id, zip_code=zip_code, page=page, penny_mode=False)

    @app_commands.command(name="home_depot_penny_hunt", description="Start a standing Home Depot penny hunt: scans now, then auto-scans daily for new candidates.")
    @app_commands.describe(
        query="Targeted query, like faucet, vanity, milwaukee, ryobi, ceiling fan.",
        store_id="Optional Home Depot store ID. Leave blank to use your saved store.",
        zip_code="Optional local ZIP. Leave blank to use your saved Home Depot ZIP.",
        page="Page to scan. Page 2+ uses another SerpApi credit.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def home_depot_penny_hunt(
        self,
        interaction: discord.Interaction,
        query: str,
        store_id: str | None = None,
        zip_code: str | None = None,
        page: app_commands.Range[int, 1, 5] = 1,
    ) -> None:
        await self._run_search(interaction, query=query, store_id=store_id, zip_code=zip_code, page=page, penny_mode=True)

    async def _run_search(
        self,
        interaction: discord.Interaction,
        query: str,
        store_id: str | None,
        zip_code: str | None,
        page: int,
        penny_mode: bool,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        cleaned_query = clean_query(query)
        cleaned_store_id = (store_id or "").strip()
        cleaned_zip_code = (zip_code or "").strip()
        # Saved Home Depot location fills any blank field; an explicitly
        # typed ZIP/store wins for this run and becomes the new saved
        # location. This is what stops the "type your ZIP every time"
        # loop Felix hit, and a saved ZIP counts as the local anchor for
        # the broad-query credit guard below.
        (
            cleaned_zip_code,
            cleaned_store_id,
            location_note_text,
            location_error,
        ) = await resolve_hd_location(
            getattr(self.bot, "db", None),
            user_id=interaction.user.id,
            zip_code=cleaned_zip_code,
            store_id=cleaned_store_id,
        )
        if location_error:
            await interaction.followup.send(location_error, ephemeral=True)
            return
        has_local_anchor = bool(cleaned_store_id or cleaned_zip_code)
        broad_warning = _broad_query_warning(cleaned_query)
        if not cleaned_query:
            await interaction.followup.send(f"Home Depot search needs a product term. Try {BROAD_QUERY_EXAMPLES}.", ephemeral=True)
            return
        # Check availability before the broad-query guard: a missing key is
        # the answer, not a lecture about spending credits on a dead feature.
        provider = provider_registry.get("home_depot_serpapi")
        if provider is None:
            await interaction.followup.send("Home Depot SerpApi provider is not registered yet.", ephemeral=True)
            return
        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send(health.message, ephemeral=True)
            return
        if broad_warning and not has_local_anchor:
            await interaction.followup.send(
                "SerpApi credit protected: that query is too broad without a ZIP or store ID.\n\n"
                f"{broad_warning}\n"
                "Add `zip_code:`/`store_id:` or use a tighter product term before spending a credit.",
                ephemeral=True,
            )
            return

        lock_key = ScanLockKey(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            action="home_depot_penny_hunt" if penny_mode else "home_depot_search",
            query=cleaned_query,
            page=int(page),
        )
        if not await scan_operation_locks.acquire(lock_key):
            await interaction.followup.send("That Home Depot scan is already running. I blocked the duplicate tap so it does not spend another SerpApi credit.", ephemeral=True)
            return

        try:
            quota = serpapi_quota_guard.check(interaction.user.id, cost=1)
            quota_blocked = not quota.allowed

            result = await provider.scan(
                ProviderScanRequest(
                    source_key="home_depot_serpapi",
                    query=cleaned_query,
                    page=page,
                    max_results=24,
                    metadata={
                        "store_id": cleaned_store_id,
                        "zip_code": cleaned_zip_code,
                        "requested_by": str(interaction.user.id),
                        # When the SerpApi quota guard refuses a spend, the
                        # direct gateway lane may still serve the scan for
                        # free; the SerpApi fallback is cache-only then.
                        "quota_blocked": quota_blocked,
                    },
                )
            )
            if quota_blocked and not result.candidates:
                await interaction.followup.send(f"SerpApi scan blocked: {quota.reason}", ephemeral=True)
                return
            # Charge only what SerpApi actually spent: the composite lane
            # reports serpapi_credit (0 for direct success / cache hits).
            quota_cost = int(result.metadata.get("serpapi_credit", 0 if result.metadata.get("cache_hit") else 1))
            quota_after = serpapi_quota_guard.record(interaction.user.id, cost=quota_cost)

            if result.warnings and not result.candidates:
                await interaction.followup.send("\n".join(result.warnings), ephemeral=True)
                return
            batch = build_home_depot_card_batch(result.candidates, has_local_anchor=has_local_anchor, penny_mode=penny_mode)
            track_note = None
            hunt_note = None
            if penny_mode:
                track_note = await self._track_penny_hunt_results(
                    result.candidates,
                    guild_id=interaction.guild_id,
                    store_id=cleaned_store_id,
                    zip_code=cleaned_zip_code,
                )
                hunt_note = await self._register_standing_hunt(
                    interaction,
                    query=cleaned_query,
                    store_id=cleaned_store_id,
                    zip_code=cleaned_zip_code,
                )
            summary = discord.Embed(
                title="🏚️ Home Depot Penny Hunt" if penny_mode else "🏚️ Home Depot Search",
                description=(
                    f"Searching: **{cleaned_query}**\n"
                    f"Store: `{cleaned_store_id or 'n/a'}` • ZIP: `{cleaned_zip_code or 'n/a'}` • Page: `{page}`\n"
                    f"SerpApi charged: **{quota_cost} credit(s)** • "
                    f"Usage: **{quota_after.monthly_used}/{quota_after.monthly_limit} monthly safe budget** • "
                    f"**{quota_after.daily_used}/{quota_after.daily_limit} today**\n"
                    f"Products returned: **{batch.returned_count}** • Cards shown: **{batch.shown_count}**"
                ),
                color=discord.Color.orange(),
            )
            if penny_mode:
                summary.description += f" • Penny-filtered: **{batch.filtered_count}**"
            summary.description += (
                "\nThese are **private shopper verification leads**, not confirmed in-store penny deals. "
                "Only your own store/register check can confirm a penny price."
            )
            if batch.used_raw_fallback:
                summary.add_field(
                    name="Credit protected",
                    value="SerpApi returned products, but none passed the penny threshold. Showing raw low-score results anyway so the credit is not wasted.",
                    inline=False,
                )
            if track_note:
                summary.add_field(name="🪙 Penny watch", value=track_note, inline=False)
            if hunt_note:
                summary.add_field(name="🔁 Standing hunt", value=hunt_note, inline=False)
            if location_note_text:
                summary.add_field(name="📍 Home Depot location", value=location_note_text, inline=False)
            if batch.candidates:
                summary.add_field(
                    name="Product links",
                    value="Each product card includes its own **App/Web** and **Browser Search** links so users do not have to match numbered buttons at the bottom.",
                    inline=False,
                )
            if cleaned_zip_code and not cleaned_store_id:
                summary.add_field(
                    name="ZIP used as local anchor",
                    value="No store ID needed. SniperPlug used the ZIP for local Home Depot search context; store-specific proof is still stronger when available.",
                    inline=False,
                )
            elif not has_local_anchor:
                summary.add_field(
                    name="Local proof warning",
                    value="No ZIP or store ID was supplied, so scores are weaker and local stock proof is limited.",
                    inline=False,
                )
            if broad_warning:
                summary.add_field(name="⚠️ Quota warning", value=broad_warning, inline=False)
            if result.warnings:
                summary.add_field(name="⚠️ Provider notes", value="\n".join(result.warnings[:3]), inline=False)
            if not batch.embeds:
                summary.add_field(
                    name="No products returned",
                    value="SerpApi did not return product cards for this query/location. Try a different query, ZIP, or page.",
                    inline=False,
                )
                await interaction.followup.send(embed=summary, ephemeral=True)
                return

            await send_summary_and_embeds(
                interaction,
                summary=summary,
                embeds=batch.embeds[:5],
                view=HomeDepotResultView(batch.candidates[:5]),
                ephemeral=True,
            )
        finally:
            await scan_operation_locks.release(lock_key)

    async def _track_penny_hunt_results(
        self,
        candidates: tuple[SourceCandidate, ...],
        *,
        guild_id: int | None,
        store_id: str,
        zip_code: str,
    ) -> str | None:
        """Auto-track stage/deep-markdown hunt results in the penny tracker.

        Tracking must never break the hunt itself: any failure is swallowed
        and the search results still stand on their own.
        """

        db = getattr(self.bot, "db", None)
        if db is None:
            return None
        try:
            seeded = await seed_from_hunt_candidates(
                db,
                candidates,
                guild_id=guild_id,
                store_id=store_id or "",
                zip_code=zip_code or None,
            )
        except Exception:
            return None
        if seeded.tracked_new:
            return (
                f"Now tracking **{seeded.tracked_new}** new item(s) toward a penny. "
                "Watch them under **Home Depot penny watch** in /menu — you will "
                "get an alert here if one enters the final markdown stage or "
                "drops to $0.01."
            )
        if seeded.tracked_existing:
            return "The stage-worthy results above are already on the penny watch list."
        return None

    async def _register_standing_hunt(
        self,
        interaction: discord.Interaction,
        *,
        query: str,
        store_id: str,
        zip_code: str,
    ) -> str | None:
        """Register (or refresh) the standing hunt behind a manual hunt.

        The manual pass the user just ran is the hunt's first pass, so
        registration stamps it as just-run and the daily discovery cycle
        takes it from there. Registration must never break the hunt
        itself: any failure is swallowed and the results still stand.
        """

        db = getattr(self.bot, "db", None)
        if db is None:
            return None
        try:
            await register_standing_hunt(
                db,
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                query=query,
                zip_code=zip_code or None,
                store_id=store_id or "",
            )
        except Exception:
            return None
        return (
            "This hunt is now a **standing auto-scan**: SniperPlug re-runs it "
            "automatically about **once a day** and seeds anything new into "
            "the penny watch — no need to run it by hand again. See or stop "
            "your hunts under **My Home Depot hunts** in /menu. Stopping a "
            "hunt does not untrack items already on the penny watch."
        )

    async def set_hd_location(
        self,
        interaction: discord.Interaction,
        zip_code: str | None = None,
        store_id: str | None = None,
    ) -> None:
        """``/menu`` → eBay & Home Depot → My Home Depot store."""

        await interaction.response.defer(ephemeral=True)
        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.followup.send(
                "I can't save your Home Depot location right now — try again in a little while.",
                ephemeral=True,
            )
            return
        try:
            preference = await save_hd_user_location(
                db,
                user_id=interaction.user.id,
                zip_code=zip_code or "",
                store_id=store_id,
            )
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        await interaction.followup.send(
            f"📍 Saved your Home Depot location: **{preference.label}**. "
            "Home Depot hunts, searches, and stock checks will use it whenever "
            "you leave the ZIP/store fields blank — you don't have to type it "
            "again. Type a different ZIP on any run to override it (that run's "
            "ZIP becomes the new saved one).",
            ephemeral=True,
        )

    async def open_my_hunts(self, interaction: discord.Interaction) -> None:
        """``/menu`` → eBay & Home Depot → My Home Depot hunts."""

        await interaction.response.defer(ephemeral=True)
        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.followup.send(
                "I can't reach your hunts right now — try again in a little while.",
                ephemeral=True,
            )
            return
        embed, hunts = await build_my_hunts_embed(
            db, guild_id=interaction.guild_id, user_id=interaction.user.id
        )
        if hunts:
            view = HdHuntStopView(db, owner_id=interaction.user.id, hunts=hunts)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            # discord.py raises TypeError when a send is handed view=None
            # explicitly; with no hunts there is nothing to attach.
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def open_penny_watch(self, interaction: discord.Interaction) -> None:
        """``/menu`` → eBay & Home Depot → Home Depot penny watch."""

        await interaction.response.defer(ephemeral=True)
        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.followup.send(
                "I can't reach the penny watch list right now — try again in a little while.",
                ephemeral=True,
            )
            return
        status = await load_hd_penny_status(db)
        location = None
        try:
            location = await get_hd_user_location(db, user_id=interaction.user.id)
        except Exception:
            location = None
        await interaction.followup.send(
            embed=build_penny_watch_embed(
                status, location_label=location.label if location else None
            ),
            ephemeral=True,
        )


async def resolve_hd_location(
    db,
    *,
    user_id: int,
    zip_code: str,
    store_id: str,
) -> tuple[str, str, str | None, str | None]:
    """Fill blank ZIP/store fields from the user's saved HD location.

    Returns ``(zip_code, store_id, note, error)``. An explicitly typed
    ZIP wins for the run and is saved as the new location (keeping the
    saved store when the ZIP is unchanged or a store was typed with it);
    an explicitly typed store alone overrides the saved store for the
    run. With both blank, the saved location fills them and the note
    says so. Validation errors come back as ``error`` text.
    """

    explicit_zip = (zip_code or "").strip()
    explicit_store = (store_id or "").strip()
    if explicit_zip:
        try:
            explicit_zip = normalize_hd_zip(explicit_zip)
        except ValueError as error:
            return "", "", None, str(error)
    if explicit_store:
        try:
            explicit_store = normalize_hd_store_id(explicit_store) or ""
        except ValueError as error:
            return "", "", None, str(error)
    saved: HdUserLocation | None = None
    if db is not None:
        try:
            saved = await get_hd_user_location(db, user_id=user_id)
        except Exception:
            saved = None
    if explicit_zip:
        store_to_save = explicit_store or (
            saved.store_id if saved is not None and saved.zip_code == explicit_zip else None
        )
        note = None
        if db is not None:
            try:
                preference = await save_hd_user_location(
                    db,
                    user_id=user_id,
                    zip_code=explicit_zip,
                    store_id=store_to_save,
                )
                note = (
                    f"Saved as your Home Depot location: **{preference.label}**. "
                    "Blank ZIP/store fields will use it from now on."
                )
            except Exception:
                note = None
        return explicit_zip, explicit_store or (store_to_save or ""), note, None
    if saved is not None:
        return (
            saved.zip_code,
            explicit_store or (saved.store_id or ""),
            f"Using your saved Home Depot location: **{saved.label}**.",
            None,
        )
    return "", explicit_store, None, None


async def build_my_hunts_embed(db, *, guild_id: int | None, user_id: int):
    """The My Home Depot hunts page: standing auto-scans + saved location."""

    hunts = await list_standing_hunts(db, guild_id=guild_id, user_id=user_id)
    location = None
    try:
        location = await get_hd_user_location(db, user_id=user_id)
    except Exception:
        location = None
    embed = discord.Embed(
        title="🔁 My Home Depot Hunts",
        description=(
            "A hunt is a **standing auto-scan**, not a one-time lookup: "
            "SniperPlug re-runs each hunt automatically about **once a "
            "day** and seeds anything new into the penny watch."
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="📍 Your Home Depot location",
        value=(
            f"**{location.label}** — blank ZIP/store fields on hunts, "
            "searches, and stock checks use it."
            if location
            else "Not set yet — set it under **My Home Depot store** in "
            "/menu, or type a ZIP on any hunt and it saves automatically."
        ),
        inline=False,
    )
    if hunts:
        lines = []
        for hunt in hunts:
            anchor = hunt.zip_code or "no ZIP"
            if hunt.store_id:
                anchor += f" · store {hunt.store_id}"
            last = (
                hunt.last_run_at.strftime("%b %d") if hunt.last_run_at else "not yet"
            )
            lines.append(f"**{hunt.query}** — {anchor} · last scan {last}")
        embed.add_field(
            name=f"Standing hunts ({len(hunts)})",
            value="\n".join(lines)[:1024],
            inline=False,
        )
        embed.add_field(
            name="Stopping a hunt",
            value=(
                "Pick a hunt below to stop its auto-scans. Items already "
                "seeded into the penny watch **stay tracked** — stopping "
                "only stops new discovery for that query."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="No standing hunts yet",
            value=(
                "Run **Home Depot penny hunt** once and it becomes a "
                "standing hunt that re-scans on its own."
            ),
            inline=False,
        )
    return embed, hunts


class HdHuntStopSelect(discord.ui.Select):
    def __init__(self, db, owner_id: int, hunts):
        self.db = db
        self.owner_id = owner_id
        self.hunts_by_id = {str(hunt.hunt_id): hunt for hunt in hunts}
        options = [
            discord.SelectOption(
                label=f"Stop: {hunt.query}"[:100],
                value=str(hunt.hunt_id),
                description="Stops auto-scans; tracked penny items stay tracked."[:100],
            )
            for hunt in hunts[:25]
        ]
        super().__init__(
            placeholder="Stop one of your standing hunts…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This hunt list belongs to the user who opened it.", ephemeral=True
            )
            return
        hunt = self.hunts_by_id.get(self.values[0])
        await interaction.response.defer(ephemeral=True)
        stopped = False
        if hunt is not None:
            stopped = await stop_standing_hunt(
                self.db, hunt_id=hunt.hunt_id, user_id=self.owner_id
            )
        embed, hunts = await build_my_hunts_embed(
            self.db, guild_id=interaction.guild_id, user_id=self.owner_id
        )
        if hunt is not None and stopped:
            embed.add_field(
                name="Stopped",
                value=(
                    f"**{hunt.query}** will not auto-scan again. Items it "
                    "already seeded stay on the penny watch."
                ),
                inline=False,
            )
        view = HdHuntStopView(self.db, owner_id=self.owner_id, hunts=hunts) if hunts else None
        await interaction.edit_original_response(embed=embed, view=view)


class HdHuntStopView(discord.ui.View):
    def __init__(self, db, owner_id: int, hunts):
        super().__init__(timeout=300)
        self.add_item(HdHuntStopSelect(db, owner_id, hunts))


def build_penny_watch_embed(status: HdPennyStatus, *, location_label: str | None = None) -> discord.Embed:
    """The penny watch status page: plain words, estimates labeled."""

    if not status.key_configured:
        state_line = (
            "⏸️ **Not running yet** — Home Depot penny tracking needs the "
            "SerpApi key (`SERPAPI_API_KEY`). Once the server owner sets it, "
            "tracking starts on its own."
        )
    elif not status.enabled:
        state_line = "⏸️ **Turned off** in the bot's settings right now."
    elif status.watching or status.pennied:
        state_line = (
            "🟢 **Watching** — tracked clearance items are re-checked on a "
            "schedule, final-stage items first."
        )
    else:
        state_line = (
            "🟢 **Running, nothing tracked yet** — run a Home Depot penny hunt "
            "or save a Home Depot clearance lead, and stage-worthy items are "
            "tracked automatically."
        )
    embed = discord.Embed(
        title="🪙 Home Depot Penny Watch",
        description=(
            f"{state_line}\n\n"
            "Clearance prices end in a code: **.03 is the final markdown** — "
            "those items drop to **$0.01** next, or get pulled. This page "
            "shows where every tracked item stands."
        ),
        color=discord.Color.orange(),
    )
    if location_label:
        embed.add_field(
            name="📍 Your Home Depot location",
            value=f"**{location_label}** — hunts and searches use it when the ZIP/store fields are blank.",
            inline=False,
        )
    counts = status.stage_counts
    embed.add_field(
        name="Where things stand",
        value=(
            f"Watching **{status.watching}** item(s) • "
            f"final stage **{counts.get('final', 0)}** • "
            f"mid **{counts.get('mid', 0)}** • "
            f"first markdown **{counts.get('first', 0)}** • "
            f"stage unclear **{counts.get('unknown', 0) + counts.get('not_clearance', 0)}**\n"
            f"Hit a penny so far: **{status.pennied}**"
            + (
                f"\nLast check: `{status.last_cycle_at}`"
                if status.last_cycle_at
                else ""
            )
        ),
        inline=False,
    )
    look_for = [item for item in status.items if item.stage in (PennyStage.FINAL, PennyStage.PENNY)]
    watching = [item for item in status.items if item.stage not in (PennyStage.FINAL, PennyStage.PENNY)]
    if look_for:
        embed.add_field(
            name="🚨 Ones to look for",
            value="\n".join(_penny_watch_item_line(item) for item in look_for[:8])[:1024],
            inline=False,
        )
    if watching:
        embed.add_field(
            name="Also being watched",
            value="\n".join(_penny_watch_item_line(item) for item in watching[:8])[:1024],
            inline=False,
        )
    if not status.items and status.key_configured and status.enabled:
        embed.add_field(
            name="Nothing tracked yet",
            value=(
                "Run **Home Depot penny hunt** (your saved ZIP is used when "
                "you leave it blank, and the hunt keeps auto-scanning daily), or use "
                "**Save a clearance lead** for a Home Depot item you spotted. "
                "Anything sitting at a clearance stage gets followed here "
                "until it drops to a penny or leaves clearance."
            ),
            inline=False,
        )
    embed.set_footer(
        text="Drop windows are estimates — timing varies by store. A penny is only called when the price is actually seen at $0.01."
    )
    return embed


def _penny_watch_item_line(item) -> str:
    price = f"${item.price_cents / 100:,.2f}" if item.price_cents is not None else "price unknown"
    stage_text = {
        PennyStage.PENNY: "🚨 **PENNY — $0.01**",
        PennyStage.FINAL: "🏷️ **Final markdown**",
        PennyStage.MID: "Mid clearance",
        PennyStage.FIRST: "First markdown",
        PennyStage.UNKNOWN: "Clearance watch",
        PennyStage.NOT_CLEARANCE: "Not on clearance",
    }[item.stage]
    bits = [f"**{_trim_title(item.title, 60)}** — {price} • {stage_text}"]
    detail: list[str] = []
    if item.store_label and item.store_label != "online":
        detail.append(f"store {item.store_label}")
    if item.days_in_stage is not None and item.status == "watching":
        detail.append(f"{item.days_in_stage:.0f} day(s) at this stage")
    if item.estimate is not None:
        estimate = item.estimate
        detail.append(
            "estimated drop "
            f"{estimate.earliest.strftime('%b %d')}–{estimate.latest.strftime('%b %d')} (estimate)"
        )
    if detail:
        bits.append(" · ".join(detail))
    return "\n".join(bits)


def _trim_title(title: str, limit: int) -> str:
    cleaned = " ".join(str(title or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def build_home_depot_cards(candidates: tuple[SourceCandidate, ...], *, has_store_id: bool, penny_mode: bool) -> list[discord.Embed]:
    return build_home_depot_card_batch(candidates, has_local_anchor=has_store_id, penny_mode=penny_mode).embeds


class HomeDepotResultView(discord.ui.View):
    def __init__(self, candidates: list[SourceCandidate]):
        super().__init__(timeout=300)
        # Product links are rendered inside each card, not as numbered bottom buttons.


def build_home_depot_card_batch(candidates: tuple[SourceCandidate, ...], *, has_local_anchor: bool, penny_mode: bool) -> HomeDepotCardBatch:
    scored: list[tuple[int, discord.Embed, SourceCandidate]] = []
    filtered_count = 0
    for candidate in candidates:
        penny = score_penny_candidate(candidate, has_store_id=has_local_anchor)
        if penny_mode and penny.score < 25:
            filtered_count += 1
            continue
        embed = build_home_depot_deal_card(candidate, penny.score, penny.level, penny.reasons, raw_fallback=False)
        scored.append((penny.score, embed, candidate))

    used_raw_fallback = False
    if penny_mode and not scored and candidates:
        used_raw_fallback = True
        filtered_count = len(candidates)
        for candidate in candidates:
            penny = score_penny_candidate(candidate, has_store_id=has_local_anchor)
            embed = build_home_depot_deal_card(candidate, penny.score, penny.level, penny.reasons, raw_fallback=True)
            scored.append((penny.score, embed, candidate))

    scored.sort(key=lambda item: item[0], reverse=True)
    embeds = [embed for _, embed, _ in scored]
    shown_candidates = [candidate for _, _, candidate in scored]
    return HomeDepotCardBatch(
        embeds=embeds,
        candidates=shown_candidates,
        returned_count=len(candidates),
        shown_count=min(len(embeds), 5),
        filtered_count=filtered_count if penny_mode else 0,
        used_raw_fallback=used_raw_fallback,
    )


def build_home_depot_deal_card(
    candidate: SourceCandidate,
    penny_score: int,
    penny_level: str,
    reasons: tuple[str, ...],
    *,
    raw_fallback: bool = False,
) -> discord.Embed:
    label = "RAW SERPAPI RESULT" if raw_fallback else home_depot_label(penny_score)
    title = f"{home_depot_heat_emoji(candidate.current_price, penny_score)} {label} • {trim_title(candidate.title, 72)}"
    embed = discord.Embed(title=title, url=candidate.product_url, color=home_depot_color(penny_score))
    if candidate.image_url:
        embed.set_thumbnail(url=candidate.image_url)

    embed.add_field(name="💰 Price", value=home_depot_price_block(candidate), inline=False)
    embed.add_field(name="🔗 Product links", value=home_depot_link_block(candidate), inline=False)
    if candidate.stock_status:
        embed.add_field(name="📦 Stock", value=home_depot_stock_block(candidate), inline=True)
    embed.add_field(
        name="🏪 What this proves",
        value=(
            "This card shows the online/search price only. "
            "It does **not** prove shelf stock, the exact local store price, or a register penny. "
            "Confirm those yourself in the selected Home Depot store before buying or sharing publicly."
        ),
        inline=False,
    )
    embed.set_footer(text="SniperPlug · online price — confirm in store before buying")
    return embed


def home_depot_link_block(candidate: SourceCandidate) -> str:
    choices = product_link_choices(
        retailer=candidate.retailer,
        product_url=candidate.product_url,
        title=candidate.title,
        product_id=candidate.product_id,
        sku=candidate.sku,
        asin=candidate.product_id if candidate.product_id_type == "asin" else None,
    )
    lines = []
    for choice in choices[:2]:
        label = choice.label.replace("Open ", "").strip() or "Open"
        lines.append(f"[{label}]({choice.url})")
    return " • ".join(lines) if lines else f"[App/Web]({candidate.product_url})"


def home_depot_price_block(candidate: SourceCandidate) -> str:
    current_price = candidate.current_price
    attrs = candidate.variant_attributes or {}
    if current_price is None:
        return "Current price: **Unavailable**\nHome Depot/SerpApi did not return a current price for this result."
    ending = price_ending(current_price)
    ending_line = f"\nEnding: **.{ending}**" if ending else ""
    badge_line = f"\nBadge: **{attrs['price_badge']}**" if attrs.get("price_badge") else ""
    if candidate.typical_price and candidate.typical_price > current_price:
        savings = candidate.typical_price - current_price
        discount = (savings / candidate.typical_price) * 100
        return (
            f"**{money(current_price)}**\n"
            f"Was/typical: **{money(candidate.typical_price)}**\n"
            f"Save: **{money(savings)} ({discount:.0f}%)**{ending_line}{badge_line}"
        )
    savings_text = attrs.get("price_saving")
    percent_text = attrs.get("percentage_off")
    if savings_text or percent_text:
        return (
            f"**{money(current_price)}**\n"
            "Was/typical: **Unavailable from SerpApi**\n"
            f"Home Depot savings text: **{savings_text or 'n/a'} {f'({percent_text})' if percent_text else ''}**{ending_line}{badge_line}"
        )
    return f"**{money(current_price)}**\nWas/typical: **Unavailable from SerpApi**\nSavings: **Not calculable from returned data**{ending_line}{badge_line}"




def home_depot_stock_block(candidate: SourceCandidate) -> str:
    lines: list[str] = []
    if candidate.stock_status:
        lines.append(candidate.stock_status[:120])
    for signal in candidate.signals:
        if signal.startswith("store_id:") or signal.startswith("zip:"):
            lines.append(signal)
    return "\n".join(lines) if lines else "Local stock not confirmed"




def home_depot_heat_emoji(current_price: float | None, penny_score: int) -> str:
    if current_price is not None and price_ending(current_price) == "01":
        return "🚨"
    if penny_score >= 80:
        return "🚨"
    if penny_score >= 60:
        return "🔥"
    if penny_score >= 40:
        return "💎"
    if penny_score >= 20:
        return "🟡"
    return "✅"


def home_depot_label(penny_score: int) -> str:
    if penny_score >= 80:
        return "HIGH-PRIORITY VERIFY"
    if penny_score >= 60:
        return "STRONG CLEARANCE LEAD"
    if penny_score >= 40:
        return "CLEARANCE WATCH"
    if penny_score >= 20:
        return "DEAL WATCH"
    return "HOME DEPOT LEAD"


def home_depot_color(penny_score: int) -> discord.Color:
    if penny_score >= 80:
        return discord.Color.red()
    if penny_score >= 60:
        return discord.Color.orange()
    if penny_score >= 40:
        return discord.Color.gold()
    return discord.Color.light_grey()



def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def price_ending(price: float | None) -> str | None:
    if price is None:
        return None
    cents = int(round((price - int(price)) * 100))
    if cents < 0 or cents > 99:
        return None
    return f"{cents:02d}"


def trim_title(title: str, limit: int) -> str:
    cleaned = " ".join(title.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def clean_query(query: str) -> str:
    return " ".join(str(query or "").split())


def _broad_query_warning(query: str) -> str | None:
    normalized = clean_query(query).lower()
    if normalized in BROAD_QUERY_TERMS:
        return f"This is a broad query and can waste credits. Use tighter terms like {BROAD_QUERY_EXAMPLES}."
    return None
