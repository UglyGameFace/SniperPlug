from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.deal import utc_now_iso
from sniperplug.services.autoscan_history import format_latest_report_line, latest_autoscan_report
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent, set_starting_deal_percent
from sniperplug.services.deal_category_preferences import (
    CATEGORY_MODE_MUTED,
    CATEGORY_MODE_NORMAL,
    CATEGORY_MODE_PRIORITY,
    apply_preset,
    categories_for_group_page,
    category_group,
    category_group_count,
    format_category_group_page,
    get_category_preferences,
    mode_label,
    normalize_category_mode,
    reset_category_preferences,
    set_category_preference,
    summarize_category_preferences,
    dashboard_quick_state,
)

from sniperplug.services.public_alert_config import get_public_alert_config, set_public_alert_config
from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.public_posting import (
    SUPPORTED_RETAILERS,
    format_retailers,
    normalize_retailer_key,
    parse_retailer_list,
)


DEFAULT_AUTOSCAN_INTERVAL_HOURS = 6
DEFAULT_AUTOSCAN_DAILY_LIMIT = 25
UNLIMITED_AUTOSCAN_INTERVAL_HOURS = 0
UNLIMITED_AUTOSCAN_DAILY_LIMIT = 0
UNMETERED_OFFICIAL_RETAILERS = {"walmart"}
WALMART_AUTOSCAN_SCAN_KEY = "autoscan:walmart_discovery"


class DealCategoryDashboardView(discord.ui.View):
    def __init__(self, db, guild_id: int, preferences: dict[str, str], *, page: int = 0, selected_key: str | None = None):
        super().__init__(timeout=300)
        self.db = db
        self.guild_id = int(guild_id)
        self.preferences = dict(preferences)
        self.page = max(0, int(page)) % category_group_count()
        self.selected_key = selected_key
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.clear_items()
        self.add_item(DealCategorySelect(self))
        self.add_item(DealCategoryModeButton(self, CATEGORY_MODE_PRIORITY, "Selected → ON", "⭐", discord.ButtonStyle.success))
        self.add_item(DealCategoryModeButton(self, CATEGORY_MODE_NORMAL, "Selected → Normal", "▫️", discord.ButtonStyle.secondary))
        self.add_item(DealCategoryModeButton(self, CATEGORY_MODE_MUTED, "Selected → Mute", "🙈", discord.ButtonStyle.danger))
        self.add_item(DealCategoryBestSetupButton(self))
        self.add_item(DealCategoryPresetButton(self, "deal_week", "Deal Week ON", "🔥", discord.ButtonStyle.primary))
        self.add_item(DealCategoryPresetButton(self, "walmart_cash", "Cash ON", "💸", discord.ButtonStyle.success))
        self.add_item(DealCategoryPresetButton(self, "flip_focus", "Flip ON", "💰", discord.ButtonStyle.primary))
        self.add_item(DealCategoryPresetButton(self, "daily_essentials", "Essentials ON", "🧻", discord.ButtonStyle.secondary))
        self.add_item(DealCategoryResetButton(self))
        self.add_item(DealCategoryPageButton(self, -1, "Prev", "⬅️"))
        self.add_item(DealCategoryPageButton(self, 1, "Next", "➡️"))

    def embed(self) -> discord.Embed:
        page_count = category_group_count()
        group_key, group_label, _categories = category_group(self.page)
        selected = f"`{self.selected_key}`" if self.selected_key else "Pick a category below"
        embed = discord.Embed(
            title="🎛️ Deal Feed Controls",
            description=(
                "**Start here:** tap **✅ Best Setup**.\n"
                "That turns on broad Walmart Deal Week coverage plus Walmart Cash tracking.\n\n"
                "Only use the dropdown if you want to fine-tune one specific category."
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="What to tap",
            value=(
                "✅ **Best Setup** = Deal Week + Walmart Cash together\n"
                "🔥 **Deal Week ON** = broad Walmart sale coverage\n"
                "💸 **Cash ON** = Walmart Cash eligible products get boosted\n"
                "💰 **Flip ON** = resale/value categories\n"
                "🧻 **Essentials ON** = grocery/household/baby/pet needs"
            ),
            inline=False,
        )
        embed.add_field(
            name="Manual editing",
            value=(
                "1. Open the dropdown and pick a category.\n"
                "2. Tap **Selected → ON**, **Selected → Normal**, or **Selected → Mute**.\n"
                "Muted hides normal deals only — **70%+ / nuclear markdowns still break through**."
            ),
            inline=False,
        )
        embed.add_field(name="Currently in use", value=dashboard_quick_state(self.preferences), inline=False)
        embed.add_field(name="Active settings", value=summarize_category_preferences(self.preferences), inline=False)
        embed.add_field(name="Selected category", value=selected, inline=True)
        embed.add_field(name="Section", value=f"{group_label} • {self.page + 1}/{page_count}", inline=True)
        embed.add_field(name="Categories in this section", value=format_category_group_page(self.preferences, page=self.page), inline=False)
        embed.add_field(
            name="Fast presets",
            value=(
                "🔥 **Deal Week** = broad Walmart sale coverage\n"
                "💸 **Walmart Cash** = boosts Walmart Cash eligible leads as an add-on\n"
                "💰 **Flip Focus** = resale/value categories, quieter boring essentials\n"
                "🧻 **Essentials** = household/grocery/baby/pet necessities"
            ),
            inline=False,
        )
        embed.set_footer(text="Private dashboard expires after a few minutes; reopen it with /deal_categories or the Deal Categories button.")
        return embed

    async def refresh(self, interaction: discord.Interaction, *, note: str | None = None) -> None:
        self.preferences = await get_category_preferences(self.db, self.guild_id)
        self._rebuild_items()
        embed = self.embed()
        if note:
            embed.add_field(name="Saved", value=note, inline=False)
        await interaction.edit_original_response(embed=embed, view=self)


class DealCategorySelect(discord.ui.Select):
    def __init__(self, dashboard: DealCategoryDashboardView):
        self.dashboard = dashboard
        options = [
            discord.SelectOption(
                label=category.label[:100],
                value=category.key,
                description=f"{mode_label(dashboard.preferences.get(category.key, CATEGORY_MODE_NORMAL))} • {category.key}"[:100],
            )
            for category in categories_for_group_page(dashboard.page)
        ] or [discord.SelectOption(label="No categories", value="none")]
        super().__init__(placeholder="Pick a category in this section…", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        value = self.values[0]
        if value != "none":
            self.dashboard.selected_key = value
        await self.dashboard.refresh(interaction, note=f"Selected `{self.dashboard.selected_key}`. Now tap **Turn ON**, **Normal**, or **Mute**.")


class DealCategoryModeButton(discord.ui.Button):
    def __init__(self, dashboard: DealCategoryDashboardView, mode: str, label: str, emoji: str, style: discord.ButtonStyle):
        super().__init__(label=label, emoji=emoji, style=style, row=1)
        self.dashboard = dashboard
        self.mode = mode

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not self.dashboard.selected_key:
            await self.dashboard.refresh(interaction, note="For quick setup, tap **✅ Best Setup**. For manual edits, pick a category from the dropdown first, then tap a Selected button.")
            return
        await set_category_preference(self.dashboard.db, self.dashboard.guild_id, self.dashboard.selected_key, self.mode)
        await self.dashboard.refresh(interaction, note=f"`{self.dashboard.selected_key}` is now **{mode_label(self.mode)}**.")


class DealCategoryBestSetupButton(discord.ui.Button):
    def __init__(self, dashboard: DealCategoryDashboardView):
        super().__init__(label="Best Setup", emoji="✅", style=discord.ButtonStyle.success, row=2)
        self.dashboard = dashboard

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await apply_preset(self.dashboard.db, self.dashboard.guild_id, "deal_week")
        await apply_preset(self.dashboard.db, self.dashboard.guild_id, "walmart_cash")
        await self.dashboard.refresh(
            interaction,
            note="Applied **Best Setup**: broad Walmart Deal Week coverage plus Walmart Cash eligible deal boosts.",
        )


class DealCategoryPresetButton(discord.ui.Button):
    def __init__(self, dashboard: DealCategoryDashboardView, preset: str, label: str, emoji: str, style: discord.ButtonStyle):
        super().__init__(label=label, emoji=emoji, style=style, row=2)
        self.dashboard = dashboard
        self.preset = preset

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await apply_preset(self.dashboard.db, self.dashboard.guild_id, self.preset)
        await self.dashboard.refresh(interaction, note=f"Applied **{self.label}** preset.")


class DealCategoryResetButton(discord.ui.Button):
    def __init__(self, dashboard: DealCategoryDashboardView):
        super().__init__(label="Reset", emoji="♻️", style=discord.ButtonStyle.danger, row=3)
        self.dashboard = dashboard

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await reset_category_preferences(self.dashboard.db, self.dashboard.guild_id)
        self.dashboard.selected_key = None
        await self.dashboard.refresh(interaction, note="Reset all category preferences to Normal.")


class DealCategoryPageButton(discord.ui.Button):
    def __init__(self, dashboard: DealCategoryDashboardView, delta: int, label: str, emoji: str):
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=4)
        self.dashboard = dashboard
        self.delta = int(delta)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        self.dashboard.page = (self.dashboard.page + self.delta) % category_group_count()
        self.dashboard.selected_key = None
        await self.dashboard.refresh(interaction)


class OpenDealCategoriesButton(discord.ui.Button):
    def __init__(self, db, guild_id: int):
        super().__init__(label="Deal Categories", emoji="🏷️", style=discord.ButtonStyle.primary)
        self.db = db
        self.guild_id = int(guild_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        preferences = await get_category_preferences(self.db, self.guild_id)
        view = DealCategoryDashboardView(self.db, self.guild_id, preferences)
        await interaction.followup.send(embed=view.embed(), view=view, ephemeral=True)


class DealCategoriesShortcutView(discord.ui.View):
    def __init__(self, db, guild_id: int):
        super().__init__(timeout=300)
        self.add_item(OpenDealCategoriesButton(db, guild_id))


class PublicAlertsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="public_alerts", description="Turn public deal posting on/off and choose the public alert channel.")
    @app_commands.describe(
        enabled="Whether verified public deal cards may post publicly.",
        channel="Channel for public deal cards. Omit to keep the existing channel or use the current channel.",
        retailers="Comma-separated stores allowed to post publicly. Example: walmart,home_depot",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def public_alerts(
        self,
        interaction: discord.Interaction,
        enabled: bool = True,
        channel: discord.TextChannel | None = None,
        retailers: str = "walmart",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which public-alert settings to save.", ephemeral=True)
            return

        existing = await get_public_alert_config(self.bot.db, interaction.guild_id)
        parsed_retailers = parse_retailer_list(retailers) or tuple(existing.get("retailers") or ()) or ("walmart",)
        unsupported = [piece.strip() for piece in retailers.replace(";", ",").split(",") if piece.strip() and normalize_retailer_key(piece) not in SUPPORTED_RETAILERS]
        if unsupported:
            await interaction.followup.send(f"Unsupported retailer(s): `{', '.join(unsupported)}`. Supported: {format_retailers(tuple(sorted(SUPPORTED_RETAILERS)))}", ephemeral=True)
            return

        chosen_channel = channel
        if chosen_channel is None and isinstance(interaction.channel, discord.TextChannel):
            chosen_channel = interaction.channel
        channel_id = chosen_channel.id if chosen_channel is not None else existing.get("channel_id")
        if enabled and channel_id is None:
            await interaction.followup.send("Public alerts need a channel. Re-run this with `channel:#your-deals-channel` or run it inside the channel you want to use.", ephemeral=True)
            return
        if enabled and chosen_channel is not None and interaction.guild is not None:
            missing = public_alert_channel_missing_permissions(chosen_channel, interaction.guild.me)
            if missing:
                await interaction.followup.send(public_alert_channel_missing_permissions_message(chosen_channel, missing), ephemeral=True)
                return

        await set_public_alert_config(
            self.bot.db,
            guild_id=interaction.guild_id,
            enabled=bool(enabled),
            retailers=parsed_retailers,
            channel_id=channel_id,
        )
        config = await get_public_alert_config(self.bot.db, interaction.guild_id)
        auto_scan = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        embed = public_alert_status_embed(
            enabled=config["enabled"],
            retailers=config["retailers"],
            channel_id=config["channel_id"],
            auto_scan=auto_scan,
            threshold=await get_starting_deal_percent(self.bot.db, interaction.guild_id),
        )
        embed.title = "📣 Public Alerts Updated"
        await interaction.followup.send(embed=embed, ephemeral=True)

    @public_alerts.error
    async def public_alerts_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to change public alerts." if isinstance(error, app_commands.MissingPermissions) else f"Public alerts hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="retailer_autoscan", description="Turn scheduled auto-scan on/off for a retailer and set its safety gates.")
    @app_commands.describe(
        retailer="Retailer key, like walmart. Supported stores are shown in /retailer_autoscan_status.",
        enabled="Whether scheduled/background auto-scan may run for this retailer.",
        interval_hours="Hours between scheduled runs. Use 0 only for official/unmetered providers like Walmart.",
        daily_limit="Max scheduled runs per day. Use 0 only for official/unmetered providers like Walmart.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def retailer_autoscan(
        self,
        interaction: discord.Interaction,
        retailer: str,
        enabled: bool,
        interval_hours: app_commands.Range[int, 0, 168] = DEFAULT_AUTOSCAN_INTERVAL_HOURS,
        daily_limit: app_commands.Range[int, 0, 250] = DEFAULT_AUTOSCAN_DAILY_LIMIT,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which auto-scan settings to save.", ephemeral=True)
            return
        key = normalize_retailer_key(retailer)
        if key not in SUPPORTED_RETAILERS:
            await interaction.followup.send(f"Unsupported retailer `{retailer}`. Supported: {format_retailers(tuple(sorted(SUPPORTED_RETAILERS)))}", ephemeral=True)
            return

        safe_interval = int(interval_hours)
        safe_daily = int(daily_limit)
        if key not in UNMETERED_OFFICIAL_RETAILERS:
            if safe_interval <= 0:
                safe_interval = DEFAULT_AUTOSCAN_INTERVAL_HOURS
            if safe_daily <= 0:
                safe_daily = DEFAULT_AUTOSCAN_DAILY_LIMIT

        await set_retailer_auto_scan(
            self.bot.db,
            interaction.guild_id,
            key,
            bool(enabled),
            interval_hours=safe_interval,
            daily_limit=safe_daily,
        )
        settings = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        embed = discord.Embed(
            title="Retailer auto-scan updated",
            description=(
                f"`{key}` scheduled auto-scan is now **{'on' if enabled else 'off'}**.\n"
                "Manual `/deals`, `/hunt`, and `/discover` are still allowed even when background auto-scan is off."
            ),
            color=discord.Color.green() if enabled else discord.Color.orange(),
        )
        embed.add_field(name="Current auto-scan settings", value=format_auto_scan_status(settings), inline=False)
        if key in UNMETERED_OFFICIAL_RETAILERS and safe_interval <= 0 and safe_daily <= 0:
            embed.add_field(name="Credit safety", value="Official/unmetered Walmart auto-scan bypass is enabled: no interval gate and no daily gate.", inline=False)
        elif int(interval_hours) <= 0 or int(daily_limit) <= 0:
            embed.add_field(name="Credit safety adjusted", value="This retailer is not marked official/unmetered, so zero gates were restored to safe defaults.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @retailer_autoscan.error
    async def retailer_autoscan_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to change retailer auto-scan." if isinstance(error, app_commands.MissingPermissions) else f"Retailer auto-scan hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="retailer_autoscan_status", description="Show scheduled auto-scan gates for each supported retailer.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def retailer_autoscan_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which auto-scan settings to show.", ephemeral=True)
            return
        settings = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        embed = discord.Embed(
            title="Retailer Auto-Scan Status",
            description="Scheduled/background scan gates. Manual commands do not depend on these being enabled.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Retailers", value=format_auto_scan_status(settings), inline=False)
        embed.add_field(name="Tip", value="Use `/retailer_autoscan retailer:walmart enabled:true interval_hours:0 daily_limit:0` for unlimited official Walmart background scans.", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @retailer_autoscan_status.error
    async def retailer_autoscan_status_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to view retailer auto-scan." if isinstance(error, app_commands.MissingPermissions) else f"Retailer auto-scan status hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="deal_categories", description="Open the category dashboard for boosting or muting deal types.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def deal_categories(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which category preferences to show.", ephemeral=True)
            return
        preferences = await get_category_preferences(self.bot.db, interaction.guild_id)
        view = DealCategoryDashboardView(self.bot.db, interaction.guild_id, preferences)
        await interaction.followup.send(embed=view.embed(), view=view, ephemeral=True)

    @deal_categories.error
    async def deal_categories_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to manage deal categories." if isinstance(error, app_commands.MissingPermissions) else f"Deal category dashboard hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="public_alerts_status", description="Show SniperPlug public posting and auto-scan settings for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def public_alerts_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which settings to show.", ephemeral=True)
            return
        config = await get_public_alert_config(self.bot.db, interaction.guild_id)
        auto_scan = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        threshold = await get_starting_deal_percent(self.bot.db, interaction.guild_id)
        embed = public_alert_status_embed(
            enabled=config["enabled"],
            retailers=config["retailers"],
            channel_id=config["channel_id"],
            auto_scan=auto_scan,
            threshold=threshold,
        )
        category_preferences = await get_category_preferences(self.bot.db, interaction.guild_id)
        embed.add_field(
            name="Category preferences",
            value=summarize_category_preferences(category_preferences),
            inline=False,
        )
        await interaction.followup.send(
            embed=embed,
            view=DealCategoriesShortcutView(self.bot.db, interaction.guild_id),
            ephemeral=True,
        )

    @app_commands.command(name="autoscan_health", description="Check whether Walmart auto-scan can post and what happened recently.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autoscan_health(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which auto-scan settings to check.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=await build_autoscan_health_embed(self.bot, interaction.guild_id),
            view=DealCategoriesShortcutView(self.bot.db, interaction.guild_id),
            ephemeral=True,
        )

    @autoscan_health.error
    async def autoscan_health_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to check auto-scan health." if isinstance(error, app_commands.MissingPermissions) else f"Auto-scan health check hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


    @app_commands.command(name="autoscan_clear_cache", description="Clear remembered active deal cache for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autoscan_clear_cache(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which active deal cache to clear.", ephemeral=True)
            return
        cleared = await clear_active_cached_deals(self.bot.db, interaction.guild_id)
        await interaction.followup.send(
            f"Cleared **{cleared}** active cached deal record(s). This does not delete Discord posts; it only resets SniperPlug's remembered active-deal cache.",
            ephemeral=True,
        )

    @autoscan_clear_cache.error
    async def autoscan_clear_cache_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to clear auto-scan cache." if isinstance(error, app_commands.MissingPermissions) else f"Auto-scan cache clear hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)



def public_alert_channel_missing_permissions(channel: discord.TextChannel, member: discord.Member | None) -> list[str]:
    if member is None:
        return []
    perms = channel.permissions_for(member)
    missing: list[str] = []
    if not getattr(perms, "view_channel", False):
        missing.append("View Channel")
    if not getattr(perms, "send_messages", False):
        missing.append("Send Messages")
    if not getattr(perms, "embed_links", False):
        missing.append("Embed Links")
    if not getattr(perms, "read_message_history", False):
        missing.append("Read Message History")
    return missing


def public_alert_channel_missing_permissions_message(channel: discord.TextChannel, missing: list[str]) -> str:
    return (
        f"SniperPlug cannot post in {channel.mention} yet.\n\n"
        "Missing channel permissions:\n"
        + "\n".join(f"• {perm}" for perm in missing)
        + "\n\nGive the SniperPlug bot/role those permissions, then run `/setup_sniperplug_here` in that channel."
    )

def public_alert_status_embed(*, enabled: bool, retailers: tuple[str, ...], channel_id: int | str | None, auto_scan: dict[str, dict] | None = None, threshold: int | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="📣 Public Alert Settings",
        description="This is the simple view. Use `/setup_sniperplug_here` for one-step setup, `/deal_categories` for boost/mute categories, `/deal_threshold` to adjust markdown, and `/autoscan_health` to diagnose posting.",
        color=discord.Color.green() if enabled else discord.Color.dark_gold(),
    )
    embed.add_field(name="Enabled", value="Yes" if enabled else "No", inline=True)
    embed.add_field(name="Public stores", value=format_retailers(retailers), inline=True)
    embed.add_field(name="Channel", value=f"<#{channel_id}>" if channel_id else "not set", inline=True)
    if threshold is not None:
        embed.add_field(name="Deal threshold", value=f"{threshold}%+ verified markdown", inline=True)
    if auto_scan is not None:
        embed.add_field(name="Auto-scan stores", value=format_auto_scan_status(auto_scan), inline=False)
    embed.add_field(name="Posting logic", value="Auto-scan uses Best Picks ranking plus category preferences. Priority categories rank higher; muted categories hide normal deals, but extreme/nuclear markdowns still override so SniperPlug does not miss amazing finds. The public guard still blocks same-price duplicates, weak proof, non-alertable cards, and low-confidence cards.", inline=False)
    embed.set_footer(text="Advanced public-alert controls are available through /retailer_autoscan and /retailer_autoscan_status.")
    return embed


async def build_autoscan_health_embed(bot: commands.Bot, guild_id: int) -> discord.Embed:
    db = bot.db
    config = await get_public_alert_config(db, guild_id)
    auto_scan = await list_retailer_auto_scan_settings(db, guild_id)
    threshold = await get_starting_deal_percent(db, guild_id)
    allowed, reason, walmart_settings = await auto_scan_allowed(db, guild_id, "walmart", scan_key=WALMART_AUTOSCAN_SCAN_KEY)
    last_run = await latest_auto_scan_run(db, guild_id, "walmart", scan_key=WALMART_AUTOSCAN_SCAN_KEY)
    latest_report = await latest_autoscan_report(db, guild_id=guild_id, retailer="walmart", scan_key=WALMART_AUTOSCAN_SCAN_KEY)
    posts_today = await count_public_posts_today(db, guild_id)
    active_cached = await count_active_cached_deals(db, guild_id)
    channel_status = public_alert_channel_status(bot, guild_id, config.get("channel_id"))
    category_preferences = await get_category_preferences(db, guild_id)

    latest_text = format_latest_report_line(latest_report)
    latest_lower = latest_text.lower()
    last_run_has_route_error = (
        "public channel lookup failed" in latest_lower
        or "ghost guild" in latest_lower
        or "bot is not currently connected to guild" in latest_lower
    )

    critical_ok = (
        bool(config.get("enabled"))
        and "walmart" in set(config.get("retailers") or ())
        and channel_status.startswith("✅")
        and bool(walmart_settings.get("enabled"))
        and allowed
        and not last_run_has_route_error
    )
    embed = discord.Embed(
        title="🩺 Walmart Auto-Scan Health",
        description="This checks setup, channel permissions, schedule gates, and the exact last run decision trail.",
        color=discord.Color.green() if critical_ok else discord.Color.orange(),
    )
    embed.add_field(
        name="Setup",
        value=(
            f"Public alerts: **{'on' if config.get('enabled') else 'off'}**\n"
            f"Public stores: {format_retailers(tuple(config.get('retailers') or ())) }\n"
            f"Threshold: **{threshold}%+ verified markdown**\n"
            f"Walmart auto-scan: **{'on' if walmart_settings.get('enabled') else 'off'}**"
        ),
        inline=False,
    )
    embed.add_field(name="Channel", value=channel_status, inline=False)
    embed.add_field(
        name="Schedule gate",
        value=(
            f"Allowed now: **{'yes' if allowed else 'no'}**\n"
            f"Reason: {reason}\n"
            f"Interval: **{format_interval(int(walmart_settings.get('interval_hours', DEFAULT_AUTOSCAN_INTERVAL_HOURS)))}**\n"
            f"Daily limit: **{format_daily_limit(int(walmart_settings.get('daily_limit', DEFAULT_AUTOSCAN_DAILY_LIMIT)))}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Recent memory",
        value=(
            f"Last scheduled run: **{last_run or 'not logged yet'}**\n"
            f"Public posts today: **{posts_today}**\n"
            f"Active cached deals: **{active_cached}**\n"
            "Cache note: active cached deals are remembered product cards, not Discord posts. "
            "Use `/autoscan_clear_cache` if stale cache noise is confusing Deal Week testing."
        ),
        inline=False,
    )
    if last_run_has_route_error:
        embed.add_field(
            name="🚨 Posting route problem",
            value=(
                "SniperPlug found candidates, but the last post attempt used a stale/ghost guild route. "
                "Run `/setup_sniperplug_here` inside the live #walmart-deals channel, then run `/autoscan_now force:true`."
            ),
            inline=False,
        )
    embed.add_field(name="Last run decision", value=trim_field(latest_text, 1024), inline=False)
    embed.add_field(
        name="How to read this",
        value="If setup/channel/gate are green but posts stay at 0, check Last run decision. It will show whether threshold, confidence, fresh filter, category preference, duplicate, not-alertable, or disabled guards blocked the candidates.",
        inline=False,
    )
    return embed


def public_alert_channel_status(bot: commands.Bot, guild_id: int, channel_id: int | str | None) -> str:
    if not channel_id:
        return "⛔ No public channel saved. Run `/setup_sniperplug_here` inside the channel you want, or `/setup_sniperplug channel:#walmart-deals`."
    guild = bot.get_guild(guild_id)
    if guild is None:
        return f"⛔ Bot is not connected to guild `{guild_id}` right now."
    decoded = decode_channel_id(channel_id)
    if decoded is None:
        return f"⛔ Saved channel ID is invalid: `{channel_id}`. Re-run `/setup_sniperplug_here`."
    channel = guild.get_channel(decoded)
    if channel is None:
        return f"⛔ Saved channel <#{decoded}> is not visible in this guild cache. Re-run `/setup_sniperplug_here` with the live channel."
    if not hasattr(channel, "send"):
        return f"⛔ Saved channel <#{decoded}> is not a sendable text channel."
    me = getattr(guild, "me", None)
    if me is not None and hasattr(channel, "permissions_for"):
        perms = channel.permissions_for(me)
        missing = []
        if not getattr(perms, "view_channel", True):
            missing.append("View Channel")
        if not getattr(perms, "send_messages", True):
            missing.append("Send Messages")
        if not getattr(perms, "embed_links", True):
            missing.append("Embed Links")
        if not getattr(perms, "read_message_history", True):
            missing.append("Read Message History")
        if missing:
            return f"⛔ <#{decoded}> is saved, but bot is missing: {', '.join(missing)}."
    return f"✅ <#{decoded}> is saved and sendable."


def decode_channel_id(value: int | str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace("<#", "").replace(">", "")
    if text.startswith("ch:"):
        text = text[3:]
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


async def latest_auto_scan_run(db, guild_id: int, retailer: str, *, scan_key: str) -> str | None:
    try:
        await ensure_retailer_auto_scan_run_table(db)
        conn = db.require_conn()
        key = normalize_retailer_key(retailer)
        cursor = await conn.execute(
            "SELECT ran_at FROM guild_retailer_auto_scan_runs WHERE guild_id = ? AND retailer = ? AND scan_key = ? ORDER BY ran_at DESC LIMIT 1",
            (guild_id, key, scan_key),
        )
        row = await cursor.fetchone()
        return str(row["ran_at"]) if row and row["ran_at"] else None
    except Exception:
        return None


async def count_public_posts_today(db, guild_id: int) -> int:
    try:
        conn = db.require_conn()
        since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS count FROM guild_public_deal_posts WHERE guild_id = ? AND status = 'posted' AND posted_at IS NOT NULL AND posted_at >= ?",
            (guild_id, since),
        )
        row = await cursor.fetchone()
        return int(row["count"] if row and row["count"] is not None else 0)
    except Exception:
        return 0


async def count_active_cached_deals(db, guild_id: int) -> int:
    try:
        conn = db.require_conn()
        cursor = await conn.execute("SELECT COUNT(*) AS count FROM guild_active_deal_cache WHERE guild_id = ? AND status = 'active'", (guild_id,))
        row = await cursor.fetchone()
        return int(row["count"] if row and row["count"] is not None else 0)
    except Exception:
        return 0


async def clear_active_cached_deals(db, guild_id: int) -> int:
    try:
        await ensure_public_post_tables(db)
        conn = db.require_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) AS count FROM guild_active_deal_cache WHERE guild_id = ? AND status = 'active'",
            (guild_id,),
        )
        row = await cursor.fetchone()
        count = int(row["count"] if row and row["count"] is not None else 0)
        await conn.execute(
            "UPDATE guild_active_deal_cache SET status = 'cleared' WHERE guild_id = ? AND status = 'active'",
            (guild_id,),
        )
        await conn.commit()
        return count
    except Exception:
        return 0



def trim_field(value: str, limit: int = 1024) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_auto_scan_status(settings: dict[str, dict]) -> str:
    rows = []
    for retailer in sorted(SUPPORTED_RETAILERS):
        config = settings.get(retailer, default_auto_scan_config(retailer))
        enabled = bool(config.get("enabled"))
        interval_hours = int(config.get("interval_hours") if config.get("interval_hours") is not None else DEFAULT_AUTOSCAN_INTERVAL_HOURS)
        daily_limit = int(config.get("daily_limit") if config.get("daily_limit") is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)
        rows.append(f"{'✅' if enabled else '⛔'} `{retailer}` • {format_interval(interval_hours)} • {format_daily_limit(daily_limit)}")
    return "\n".join(rows)


def format_interval(interval_hours: int) -> str:
    return "no interval gate" if int(interval_hours) <= 0 else f"every {int(interval_hours)}h"


def format_daily_limit(daily_limit: int) -> str:
    return "no daily gate" if int(daily_limit) <= 0 else f"max {int(daily_limit)}/day"


def default_auto_scan_config(retailer: str) -> dict:
    key = normalize_retailer_key(retailer)
    return {"retailer": key, "enabled": False, "interval_hours": DEFAULT_AUTOSCAN_INTERVAL_HOURS, "daily_limit": DEFAULT_AUTOSCAN_DAILY_LIMIT}


async def ensure_retailer_auto_scan_table(db) -> None:
    conn = db.require_conn()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_retailer_auto_scan_settings (
            guild_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            interval_hours INTEGER NOT NULL DEFAULT 6,
            daily_limit INTEGER NOT NULL DEFAULT 25,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, retailer)
        )
    """)
    await maybe_add_column(conn, "guild_retailer_auto_scan_settings", "interval_hours", "INTEGER NOT NULL DEFAULT 6")
    await maybe_add_column(conn, "guild_retailer_auto_scan_settings", "daily_limit", "INTEGER NOT NULL DEFAULT 25")
    await conn.commit()


async def ensure_retailer_auto_scan_run_table(db) -> None:
    conn = db.require_conn()
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_retailer_auto_scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            retailer TEXT NOT NULL,
            scan_key TEXT NOT NULL,
            ran_at TEXT NOT NULL,
            day_key TEXT NOT NULL
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_scan_runs_guild_retailer_day ON guild_retailer_auto_scan_runs (guild_id, retailer, day_key)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_auto_scan_runs_guild_retailer_key ON guild_retailer_auto_scan_runs (guild_id, retailer, scan_key, ran_at)")
    await conn.commit()


async def maybe_add_column(conn, table: str, column: str, definition: str) -> None:
    try:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception as exc:
        if "duplicate column" not in str(exc).lower():
            raise


async def set_retailer_auto_scan(db, guild_id: int, retailer: str, enabled: bool, *, interval_hours: int | None = None, daily_limit: int | None = None) -> None:
    await ensure_retailer_auto_scan_table(db)
    conn = db.require_conn()
    now = utc_now_iso()
    key = normalize_retailer_key(retailer)
    existing = (await list_retailer_auto_scan_settings(db, guild_id)).get(key, default_auto_scan_config(key))
    next_interval = interval_hours if interval_hours is not None else int(existing.get("interval_hours") if existing.get("interval_hours") is not None else DEFAULT_AUTOSCAN_INTERVAL_HOURS)
    next_daily_limit = daily_limit if daily_limit is not None else int(existing.get("daily_limit") if existing.get("daily_limit") is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)
    await conn.execute("""
        INSERT INTO guild_retailer_auto_scan_settings (guild_id, retailer, enabled, interval_hours, daily_limit, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, retailer) DO UPDATE SET
            enabled = excluded.enabled,
            interval_hours = excluded.interval_hours,
            daily_limit = excluded.daily_limit,
            updated_at = excluded.updated_at
    """, (guild_id, key, int(enabled), next_interval, next_daily_limit, now, now))
    await conn.commit()


async def list_retailer_auto_scan_settings(db, guild_id: int) -> dict[str, dict]:
    await ensure_retailer_auto_scan_table(db)
    conn = db.require_conn()
    cursor = await conn.execute("SELECT retailer, enabled, interval_hours, daily_limit FROM guild_retailer_auto_scan_settings WHERE guild_id = ?", (guild_id,))
    rows = await cursor.fetchall()
    settings = {retailer: default_auto_scan_config(retailer) for retailer in SUPPORTED_RETAILERS}
    for row in rows:
        key = normalize_retailer_key(row["retailer"])
        if key in SUPPORTED_RETAILERS:
            settings[key] = {"retailer": key, "enabled": bool(row["enabled"]), "interval_hours": int(row["interval_hours"] if row["interval_hours"] is not None else DEFAULT_AUTOSCAN_INTERVAL_HOURS), "daily_limit": int(row["daily_limit"] if row["daily_limit"] is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)}
    return settings


async def auto_scan_allowed(db, guild_id: int, retailer: str, *, scan_key: str) -> tuple[bool, str, dict]:
    key = normalize_retailer_key(retailer)
    settings = (await list_retailer_auto_scan_settings(db, guild_id)).get(key, default_auto_scan_config(key))
    if not settings.get("enabled"):
        return False, f"`{key}` auto-scan is off", settings
    daily_limit = int(settings.get("daily_limit") if settings.get("daily_limit") is not None else DEFAULT_AUTOSCAN_DAILY_LIMIT)
    interval_hours = int(settings.get("interval_hours") if settings.get("interval_hours") is not None else DEFAULT_AUTOSCAN_INTERVAL_HOURS)
    bypass_gates = key in UNMETERED_OFFICIAL_RETAILERS and daily_limit <= 0 and interval_hours <= 0
    if not bypass_gates and daily_limit <= 0:
        return False, f"`{key}` daily auto-scan limit is 0", settings
    await ensure_retailer_auto_scan_run_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    day_key = now.date().isoformat()
    cursor = await conn.execute("SELECT COUNT(*) AS count FROM guild_retailer_auto_scan_runs WHERE guild_id = ? AND retailer = ? AND day_key = ?", (guild_id, key, day_key))
    row = await cursor.fetchone()
    used_today = int(row["count"] if row and row["count"] is not None else 0)
    if not bypass_gates and used_today >= daily_limit:
        return False, f"`{key}` daily auto-scan limit reached ({used_today}/{daily_limit})", settings
    if not bypass_gates and interval_hours > 0:
        cursor = await conn.execute("SELECT ran_at FROM guild_retailer_auto_scan_runs WHERE guild_id = ? AND retailer = ? AND scan_key = ? ORDER BY ran_at DESC LIMIT 1", (guild_id, key, scan_key))
        last = await cursor.fetchone()
        if last and last["ran_at"]:
            last_dt = datetime.fromisoformat(str(last["ran_at"]))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            next_allowed = last_dt + timedelta(hours=interval_hours)
            if now < next_allowed:
                minutes = max(1, int((next_allowed - now).total_seconds() // 60))
                return False, f"`{key}` interval gate: try again in about {minutes} minute(s)", settings
    if bypass_gates:
        return True, f"`{key}` auto-scan allowed with official-provider bypass ({used_today} runs logged today)", settings
    return True, f"`{key}` auto-scan allowed ({used_today}/{daily_limit} used today)", settings


async def record_auto_scan_run(db, guild_id: int, retailer: str, *, scan_key: str) -> None:
    await ensure_retailer_auto_scan_run_table(db)
    conn = db.require_conn()
    key = normalize_retailer_key(retailer)
    now = datetime.now(timezone.utc)
    await conn.execute("INSERT INTO guild_retailer_auto_scan_runs (guild_id, retailer, scan_key, ran_at, day_key) VALUES (?, ?, ?, ?, ?)", (guild_id, key, scan_key, now.isoformat(), now.date().isoformat()))
    await conn.commit()
