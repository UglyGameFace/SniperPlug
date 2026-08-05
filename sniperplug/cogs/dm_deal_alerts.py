from __future__ import annotations

from dataclasses import replace

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    delete_dm_deal_alert_preference,
    get_dm_deal_alert_preference,
    normalize_terms,
    save_dm_deal_alert_preference,
)
from sniperplug.services.dm_personal_categories import (
    category_label,
    split_category_preferences,
    split_exclude_terms,
    update_category_mutes,
    update_favorite_categories,
)


ACTION_CHOICES = [
    app_commands.Choice(name="Enable or update alerts", value="enable"),
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
        action="Enable, inspect, test, pause, or delete your personal alerts.",
        mode="Smart adapts quality rules to price; All and Custom use hard filters.",
        min_discount="Lowest exact Walmart markdown percentage you want.",
        max_price="Do not DM products above this current price.",
        min_score="Minimum Sniper score from 0 to 250.",
        min_savings="Minimum exact dollar savings from Walmart's was price.",
        categories="Optional hard allowlist. Leave empty to keep all categories eligible.",
        favorite_categories="Prioritize interests without excluding other great deals: tech, gaming, PC, smart home.",
        unfavorite_categories="Remove categories from your personal favorites.",
        keywords="Comma list of words that must match at least one product detail.",
        exclude="Comma list of words that always block a product from your DMs.",
        mute_categories="Hide categories only from your DMs, such as baby, toys, pets, or beauty.",
        unmute_categories="Restore personally muted categories, such as baby or toys.",
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

        updated_excludes = update_category_mutes(
            preference.exclude_keywords,
            add=mute_categories,
            remove=unmute_categories,
            replacement_keywords=(
                normalize_terms(exclude)
                if exclude is not None
                else None
            ),
        )
        updated_categories = update_favorite_categories(
            preference.categories,
            add=favorite_categories,
            remove=unfavorite_categories,
            replacement_selected=categories,
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
    keyword_excludes, muted_categories = split_exclude_terms(
        normalized.exclude_keywords
    )
    selected_categories, favorite_categories = split_category_preferences(
        normalized.categories
    )

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
        name="Favorites and mutes are personal",
        value=(
            "Favorites get a small Smart-mode priority boost but do **not** hide other great deals. "
            "Muted categories disappear only from **your** DMs. Public alerts and every other "
            "subscriber remain unchanged. Example: `favorite_categories:tech,gaming,pc` and "
            "`mute_categories:baby`."
        ),
        inline=False,
    )
    embed.add_field(
        name="How Smart mode works",
        value=(
            "Smart mode adapts percentage and dollar-savings requirements to the item's price. "
            "A favorite category may soften only Smart's additional requirement; it never goes "
            "below your explicit markdown, score, or dollar-savings minimum and never replaces "
            "exact current/was-price proof."
        ),
        inline=False,
    )
    embed.add_field(
        name="Built-in safety",
        value=(
            "Only exact-item, exact-offer, buyable Walmart deals with trusted current and was prices enter this stream. "
            "Each exact offer/price is deduplicated per user, and your daily cap prevents DM floods."
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
        text="Use /dm_deals action:Show my settings or action:Pause alerts at any time."
    )
    return embed
