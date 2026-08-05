from __future__ import annotations

from dataclasses import replace

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.dm_deal_preferences_view import DmDealPreferencesView
from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    delete_dm_deal_alert_preference,
    get_dm_deal_alert_preference,
    normalize_terms,
    save_dm_deal_alert_preference,
)
from sniperplug.services.dm_personal_categories import (
    category_label,
    compose_exclude_terms,
    flip_settings,
    muted_category_preferences,
    normalize_personal_categories,
    split_category_preferences,
    split_exclude_terms,
    update_favorite_categories,
    update_flip_settings,
    update_muted_categories,
)


ACTION_CHOICES = [
    app_commands.Choice(name="Open personalization menu", value="menu"),
    app_commands.Choice(name="Enable or update with typed options", value="enable"),
    app_commands.Choice(name="Show my settings", value="status"),
    app_commands.Choice(name="Send a test DM", value="test"),
    app_commands.Choice(name="Pause alerts", value="disable"),
    app_commands.Choice(name="Delete my alert data", value="delete"),
]

MODE_CHOICES = [
    app_commands.Choice(name="Smart — quality and value adapt to price", value="smart"),
    app_commands.Choice(name="All — use only my hard filters", value="all"),
    app_commands.Choice(name="Custom — strict manual settings", value="custom"),
]


class DmDealAlertsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="dm_deals",
        description="Set personal exact-verified deal alerts in your DMs.",
    )
    @app_commands.describe(
        action="Open the menu, inspect, test, pause, or update your personal alerts.",
        mode="Smart adapts quality rules to price; All and Custom use hard filters.",
        min_discount="Lowest exact Walmart markdown percentage you want.",
        max_price="Do not DM products above this current price, including flips.",
        min_score="Minimum Sniper score from 0 to 250.",
        min_savings="Minimum exact dollar savings from Walmart's was price.",
        categories="Optional hard allowlist. Leave empty to keep all categories eligible.",
        favorite_categories="Prioritize interests without excluding other great deals: tech, gaming, PC, smart home.",
        unfavorite_categories="Remove categories from your personal favorites.",
        keywords="Comma list of words that must match normal-interest alerts.",
        exclude="Comma list of words that always block a product, including flips.",
        mute_categories="Hide categories from normal DMs, such as baby, toys, pets, or beauty.",
        unmute_categories="Restore personally muted categories, such as baby or toys.",
        flip_alerts="Allow exceptional price-error/resell alerts across category mutes.",
        flip_min_profit="Minimum conservative estimated net profit for a flip alert.",
        walmart_cash_only="Only alert when strict Walmart API Cash proof is attached.",
        daily_cap="Maximum personal deal DMs per UTC day.",
    )
    @app_commands.choices(action=ACTION_CHOICES, mode=MODE_CHOICES)
    async def dm_deals(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        mode: app_commands.Choice[str] | None = None,
        min_discount: app_commands.Range[int, 0, 95] | None = None,
        max_price: app_commands.Range[float, 0.01, 100000.0] | None = None,
        min_score: app_commands.Range[int, 0, 250] | None = None,
        min_savings: app_commands.Range[float, 0.0, 100000.0] | None = None,
        categories: app_commands.Range[str, 1, 300] | None = None,
        favorite_categories: app_commands.Range[str, 1, 300] | None = None,
        unfavorite_categories: app_commands.Range[str, 1, 300] | None = None,
        keywords: app_commands.Range[str, 1, 300] | None = None,
        exclude: app_commands.Range[str, 1, 300] | None = None,
        mute_categories: app_commands.Range[str, 1, 300] | None = None,
        unmute_categories: app_commands.Range[str, 1, 300] | None = None,
        flip_alerts: bool | None = None,
        flip_min_profit: app_commands.Range[float, 10.0, 100000.0] | None = None,
        walmart_cash_only: bool | None = None,
        daily_cap: app_commands.Range[int, 1, 100] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = int(interaction.user.id)
        selected_action = str(action.value)

        if selected_action == "delete":
            await delete_dm_deal_alert_preference(self.bot.db, user_id)
            await interaction.followup.send(
                "🗑️ Your SniperPlug DM alert settings and delivery receipts were deleted.",
                ephemeral=True,
            )
            return

        preference = await get_dm_deal_alert_preference(self.bot.db, user_id)

        if selected_action == "menu":
            view = DmDealPreferencesView(
                bot=self.bot,
                user_id=user_id,
                preference=preference,
            )
            await interaction.followup.send(
                embed=view.build_embed(),
                view=view,
                ephemeral=True,
            )
            return

        if selected_action == "status":
            await interaction.followup.send(
                embed=build_dm_settings_embed(preference),
                ephemeral=True,
            )
            return

        if selected_action == "disable":
            saved = await save_dm_deal_alert_preference(
                self.bot.db,
                replace(preference, enabled=False),
            )
            await interaction.followup.send(
                embed=build_dm_settings_embed(
                    saved,
                    title="⏸️ Personal deal DMs paused",
                ),
                ephemeral=True,
            )
            return

        if selected_action == "test":
            try:
                await interaction.user.send(
                    embed=build_test_dm_embed(preference),
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "I could not DM you. Allow direct messages from server members for a server we share, then run `/dm_deals action:Send a test DM` again.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as error:
                await interaction.followup.send(
                    f"Discord rejected the test DM temporarily: `{type(error).__name__}`. Your saved alert settings were not changed.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                "✅ Test DM delivered. Your alerts are still "
                + ("enabled." if preference.enabled else "paused until you choose Enable or update alerts."),
                ephemeral=True,
            )
            return

        keyword_excludes, legacy_muted = split_exclude_terms(
            preference.exclude_keywords
        )
        stored_muted = muted_category_preferences(preference.categories)
        updated_muted = list(dict.fromkeys((*legacy_muted, *stored_muted)))
        updated_muted.extend(normalize_personal_categories(mute_categories))
        remove_muted = set(normalize_personal_categories(unmute_categories))
        updated_muted = [
            category
            for category in dict.fromkeys(updated_muted)
            if category not in remove_muted
        ]

        updated_excludes = compose_exclude_terms(
            normalize_terms(exclude)
            if exclude is not None
            else keyword_excludes
        )
        updated_categories = update_favorite_categories(
            preference.categories,
            add=favorite_categories,
            remove=unfavorite_categories,
            replacement_selected=categories,
        )
        updated_categories = update_muted_categories(
            updated_categories,
            replacement=updated_muted,
        )
        updated_categories = update_flip_settings(
            updated_categories,
            enabled=flip_alerts,
            minimum_profit_cents=(
                int(round(float(flip_min_profit) * 100))
                if flip_min_profit is not None
                else None
            ),
        )

        updated = replace(
            preference,
            enabled=True,
            mode=mode.value if mode is not None else preference.mode,
            min_discount=(
                int(min_discount)
                if min_discount is not None
                else preference.min_discount
            ),
            max_price_cents=(
                int(round(float(max_price) * 100))
                if max_price is not None
                else preference.max_price_cents
            ),
            min_score=(
                int(min_score) if min_score is not None else preference.min_score
            ),
            min_savings_cents=(
                int(round(float(min_savings) * 100))
                if min_savings is not None
                else preference.min_savings_cents
            ),
            categories=updated_categories,
            keywords=(
                normalize_terms(keywords)
                if keywords is not None
                else preference.keywords
            ),
            exclude_keywords=updated_excludes,
            walmart_cash_only=(
                bool(walmart_cash_only)
                if walmart_cash_only is not None
                else preference.walmart_cash_only
            ),
            max_alerts_per_day=(
                int(daily_cap)
                if daily_cap is not None
                else preference.max_alerts_per_day
            ),
            failure_count=0,
            last_error="",
        ).normalized()

        # Prove DM delivery before enabling a stream that cannot reach the user.
        try:
            await interaction.user.send(embed=build_enabled_dm_embed(updated))
        except discord.Forbidden:
            await interaction.followup.send(
                "I did not enable alerts because your DMs are closed. Allow direct messages from server members for a server we share, then run the command again.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            await interaction.followup.send(
                f"Discord could not deliver the confirmation DM (`{type(error).__name__}`), so alerts remain unchanged. Try again shortly.",
                ephemeral=True,
            )
            return

        saved = await save_dm_deal_alert_preference(self.bot.db, updated)
        await interaction.followup.send(
            embed=build_dm_settings_embed(
                saved,
                title="✅ Personal exact-deal DMs enabled",
            ),
            ephemeral=True,
        )

    @dm_deals.error
    async def dm_deals_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = (
            "Personal alert setup failed safely. No settings were changed. "
            f"Error: `{type(error).__name__}`"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def build_dm_settings_embed(
    preference: DmDealAlertPreference,
    *,
    title: str = "🔔 Personal deal DM settings",
) -> discord.Embed:
    normalized = preference.normalized()
    keyword_excludes, legacy_muted = split_exclude_terms(
        normalized.exclude_keywords
    )
    stored_muted = muted_category_preferences(normalized.categories)
    muted_categories = tuple(dict.fromkeys((*legacy_muted, *stored_muted)))
    selected_categories, favorite_categories = split_category_preferences(
        normalized.categories
    )
    flip_enabled, flip_min_profit_cents = flip_settings(normalized.categories)

    muted_text = (
        ", ".join(category_label(category) for category in muted_categories)
        if muted_categories
        else "none"
    )
    favorite_text = (
        ", ".join(category_label(category) for category in favorite_categories)
        if favorite_categories
        else "none"
    )
    selected_text = (
        ", ".join(category_label(category) for category in selected_categories)
        if selected_categories
        else "all categories"
    )
    keyword_text = ", ".join(keyword_excludes) if keyword_excludes else "none"

    summary: list[str] = []
    for line in normalized.summary_lines():
        if line.startswith("Categories:"):
            summary.append(f"Allowed categories: **{selected_text}**")
            summary.append(f"Favorite DM categories: **{favorite_text}**")
            summary.append(
                "Price-error / flip override: "
                f"**{'enabled' if flip_enabled else 'disabled'}**"
            )
            summary.append(
                "Minimum estimated flip profit: "
                f"**${flip_min_profit_cents / 100:,.2f}**"
            )
        elif line.startswith("Exclude:"):
            summary.append(f"Exclude words: **{keyword_text}**")
            summary.append(f"Muted DM categories: **{muted_text}**")
        else:
            summary.append(line)

    embed = discord.Embed(
        title=title,
        description="\n".join(summary),
        color=discord.Color.green() if normalized.enabled else discord.Color.orange(),
    )
    embed.add_field(
        name="Open the complete menu",
        value=(
            "Run `/dm_deals action:Open personalization menu` for the paginated, searchable "
            "catalog. Every current category is included, and future categories appear automatically."
        ),
        inline=False,
    )
    embed.add_field(
        name="Favorites and mutes are personal",
        value=(
            "Favorites get a small Smart-mode priority boost but do **not** hide other great deals. "
            "Muted categories disappear from normal **personal** DMs only. Public alerts and every "
            "other subscriber remain unchanged."
        ),
        inline=False,
    )
    embed.add_field(
        name="Price-error / flip override",
        value=(
            "When enabled, a significant cross-category price error may break through a category mute. "
            "Without recent sold comps, SniperPlug uses a strict conservative resale haircut, fee reserve, "
            "shipping reserve, minimum ROI, and your profit floor—and labels it **estimated**. When exact "
            "recent eBay sold evidence is connected, the alert shows sold count, median sold price, and "
            "estimated net profit. Active eBay listing prices never count as sold proof."
        ),
        inline=False,
    )
    embed.add_field(
        name="Built-in safety",
        value=(
            "Only exact-item, exact-offer, buyable Walmart deals with trusted current and was prices enter this stream. "
            "Flip Override crosses category boundaries only; explicit floors, maximum price, required/excluded words, "
            "dedupe, and the daily cap remain hard."
        ),
        inline=False,
    )
    if normalized.last_error:
        embed.add_field(
            name="Last delivery problem",
            value=normalized.last_error[:1024],
            inline=False,
        )
    return embed


def build_test_dm_embed(preference: DmDealAlertPreference) -> discord.Embed:
    embed = build_dm_settings_embed(
        preference,
        title="🧪 SniperPlug DM test",
    )
    embed.description = (
        "This confirms SniperPlug can reach your DMs. It is not a product alert.\n\n"
        + (embed.description or "")
    )
    return embed


def build_enabled_dm_embed(preference: DmDealAlertPreference) -> discord.Embed:
    embed = build_dm_settings_embed(
        preference,
        title="✅ SniperPlug personal deal alerts are ready",
    )
    embed.set_footer(
        text="Use /dm_deals action:Open personalization menu or action:Pause alerts at any time."
    )
    return embed
