from __future__ import annotations

from dataclasses import replace

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.dm_deal_preferences_view import DmDealPreferencesView
from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    UNLIMITED_MAX_ALERTS_PER_DAY,
    daily_cap_label,
    delete_dm_deal_alert_preference,
    dm_alerts_sent_today,
    get_dm_deal_alert_preference,
    normalize_terms,
    save_dm_deal_alert_preference,
)
from sniperplug.services.dm_delivery_policy import delete_dm_delivery_history
from sniperplug.services.dm_deal_routes import route_labels, split_dm_route_preferences
from sniperplug.services.dm_delivery_telemetry import (
    UserDmDeliveryTelemetry,
    clear_user_dm_delivery_telemetry,
    get_user_dm_delivery_telemetry,
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
from sniperplug.services.dm_product_interests import (
    brand_label,
    parse_product_interest_input,
    product_interest_catalog_lines,
    product_interest_label,
    split_product_preferences,
    update_product_preferences,
)


ACTION_CHOICES = [
    app_commands.Choice(name="Open personalization menu", value="menu"),
    app_commands.Choice(name="Enable or update with typed options", value="enable"),
    app_commands.Choice(name="Show specific product catalog", value="interests"),
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

_DAILY_CAP_UNLIMITED_TERMS = {
    "0",
    "off",
    "none",
    "unlimited",
    "no limit",
    "no-limit",
    "nolimit",
}


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
        product_interests="Add specific products: portable chargers, NeeDoh, Pokemon TCG, cologne, LEGO, and more.",
        remove_interests="Remove specific product interests from your personal DMs.",
        brands="Add brand filters such as Anker, Versace, Dior, YSL, Belkin, or LEGO.",
        remove_brands="Remove brands from your personal DM filter.",
        keywords="Advanced comma list: at least one of these words must match.",
        exclude="Comma list of words that always block a product, including flips.",
        mute_categories="Hide categories from normal DMs, such as baby, toys, pets, or beauty.",
        unmute_categories="Restore personally muted categories, such as baby or toys.",
        flip_alerts="Allow exceptional price-error/resell alerts across category mutes.",
        flip_min_profit="Minimum conservative estimated net profit for a flip alert.",
        walmart_cash_only="Only alert when strict Walmart API Cash proof is attached.",
        daily_cap="Daily DM limit: 1-100, or type unlimited for no cap.",
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
        product_interests: app_commands.Range[str, 1, 400] | None = None,
        remove_interests: app_commands.Range[str, 1, 400] | None = None,
        brands: app_commands.Range[str, 1, 400] | None = None,
        remove_brands: app_commands.Range[str, 1, 400] | None = None,
        keywords: app_commands.Range[str, 1, 300] | None = None,
        exclude: app_commands.Range[str, 1, 300] | None = None,
        mute_categories: app_commands.Range[str, 1, 300] | None = None,
        unmute_categories: app_commands.Range[str, 1, 300] | None = None,
        flip_alerts: bool | None = None,
        flip_min_profit: app_commands.Range[float, 10.0, 100000.0] | None = None,
        walmart_cash_only: bool | None = None,
        daily_cap: app_commands.Range[str, 1, 12] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = int(interaction.user.id)
        selected_action = str(action.value)

        if selected_action == "delete":
            await delete_dm_deal_alert_preference(self.bot.db, user_id)
            await delete_dm_delivery_history(self.bot.db, user_id=user_id)
            clear_user_dm_delivery_telemetry(user_id)
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

        if selected_action == "interests":
            await interaction.followup.send(
                embed=build_product_interest_catalog_embed(preference),
                ephemeral=True,
            )
            return

        if selected_action == "status":
            sent_today = await dm_alerts_sent_today(self.bot.db, user_id)
            telemetry = get_user_dm_delivery_telemetry(user_id)
            embed = build_dm_settings_embed(preference)
            add_dm_delivery_health_field(
                embed,
                preference=preference,
                sent_today=sent_today,
                telemetry=telemetry,
            )
            await interaction.followup.send(
                embed=embed,
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

        try:
            parsed_daily_cap = (
                _parse_daily_cap_argument(str(daily_cap))
                if daily_cap is not None
                else None
            )
        except ValueError as error:
            await interaction.followup.send(f"⚠️ {error}", ephemeral=True)
            return

        add_interest_keys, unknown_add_interests = parse_product_interest_input(
            product_interests
        )
        remove_interest_keys, unknown_remove_interests = parse_product_interest_input(
            remove_interests
        )
        unknown_interests = (*unknown_add_interests, *unknown_remove_interests)
        if unknown_interests:
            preview = ", ".join(f"`{value}`" for value in unknown_interests[:6])
            await interaction.followup.send(
                "⚠️ I did not change your settings because I could not identify "
                f"these specific product interests: {preview}. Run `/dm_deals "
                "action:Show specific product catalog` to see accepted names.",
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
        updated_categories = update_product_preferences(
            updated_categories,
            add_interests=add_interest_keys,
            remove_interests=remove_interest_keys,
            add_brands=brands,
            remove_brands=remove_brands,
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
                parsed_daily_cap
                if parsed_daily_cap is not None
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


def _parse_daily_cap_argument(value: str) -> int:
    raw = " ".join(str(value or "").strip().lower().split())
    if raw in _DAILY_CAP_UNLIMITED_TERMS:
        return UNLIMITED_MAX_ALERTS_PER_DAY
    try:
        cap = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Daily DM limit must be 1 to 100, or `unlimited`.") from exc
    if not 1 <= cap <= 100:
        raise ValueError("Daily DM limit must be between 1 and 100, or `unlimited`.")
    return cap


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
    product_interests, product_brands = split_product_preferences(
        normalized.categories
    )
    flip_enabled, flip_min_profit_cents = flip_settings(normalized.categories)
    selected_routes = split_dm_route_preferences(normalized.categories)
    route_names = dict(route_labels())
    route_text = ", ".join(route_names[key] for key in selected_routes) if selected_routes else "all verified types"

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
    interest_text = (
        ", ".join(product_interest_label(key) for key in product_interests)
        if product_interests
        else "none — category rules stay broad"
    )
    brand_text = (
        ", ".join(brand_label(slug) for slug in product_brands)
        if product_brands
        else "all brands"
    )
    keyword_text = ", ".join(keyword_excludes) if keyword_excludes else "none"

    summary: list[str] = []
    for line in normalized.summary_lines():
        if line.startswith("Categories:"):
            summary.append(f"DM deal routes: **{route_text}**")
            if selected_routes:
                summary.append("Routes are active: old product/category inclusion filters are bypassed.")
            summary.append(f"Allowed categories: **{selected_text}**")
            summary.append(f"Favorite DM categories: **{favorite_text}**")
            summary.append(f"Specific products: **{interest_text}**")
            summary.append(f"Brand filter: **{brand_text}**")
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
        name="🎯 Specific product filters",
        value=(
            "Without a route subscription, specific products narrow broad categories. "
            "Multiple product interests are **OR**; "
            "multiple brands are **OR**. If you set both products and brands, a deal must match "
            "**one selected product AND one selected brand**. A selected deal route takes priority "
            "over these product/category filters; other hard limits stay enforced.\n"
            "Run `/dm_deals action:Show specific product catalog` for the supported product profiles."
        ),
        inline=False,
    )
    embed.add_field(
        name="Open the complete menu",
        value=(
            "Run `/dm_deals action:Open personalization menu` for the paginated, searchable "
            "deal route selector, products, categories and saved personal controls."
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
            "Your active inclusion choice (routes or products/categories), floors, maximum price, "
            "required/excluded words, dedupe, and your saved "
            "daily-limit policy remain hard. Broad Smart alerts with a finite limit also reserve room for category "
            "variety; Unlimited removes both the total cap and automatic family quota."
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


def build_product_interest_catalog_embed(
    preference: DmDealAlertPreference,
) -> discord.Embed:
    interests, brands = split_product_preferences(preference.categories)
    selected_products = (
        ", ".join(product_interest_label(key) for key in interests)
        if interests
        else "none"
    )
    selected_brands = (
        ", ".join(brand_label(slug) for slug in brands)
        if brands
        else "all brands"
    )
    lines = product_interest_catalog_lines()
    embed = discord.Embed(
        title="🎯 Specific personal DM products",
        description=(
            "These are intentionally narrower than categories. Add them with `/dm_deals "
            "action:Enable or update with typed options product_interests:...` and optionally "
            "add `brands:...`.\n\n"
            f"**Selected products:** {selected_products}\n"
            f"**Selected brands:** {selected_brands}\n\n"
            "Examples: `portable chargers`, `NeeDoh`, `Pokemon TCG`, `designer cologne`, "
            "`LEGO sets`, `gaming controllers`. Brands can be any real brand name."
        ),
        color=discord.Color.blurple(),
    )
    chunk: list[str] = []
    chunk_size = 0
    field_number = 1
    for line in lines:
        projected = chunk_size + len(line) + 1
        if chunk and projected > 950:
            embed.add_field(
                name=f"Product profiles {field_number}",
                value="\n".join(chunk),
                inline=False,
            )
            field_number += 1
            chunk = []
            chunk_size = 0
        chunk.append(line)
        chunk_size += len(line) + 1
    if chunk:
        embed.add_field(
            name=f"Product profiles {field_number}",
            value="\n".join(chunk),
            inline=False,
        )
    embed.set_footer(
        text="Product filters are hard. Brand filters narrow them further; Flip Override does not bypass them."
    )
    return embed


def add_dm_delivery_health_field(
    embed: discord.Embed,
    *,
    preference: DmDealAlertPreference,
    sent_today: int,
    telemetry: UserDmDeliveryTelemetry,
) -> None:
    normalized = preference.normalized()
    if normalized.max_alerts_per_day == UNLIMITED_MAX_ALERTS_PER_DAY:
        delivery_line = (
            f"Today (UTC): **{int(sent_today)}** delivered • daily limit **unlimited**."
        )
        cap_reached = False
    else:
        remaining = max(0, normalized.max_alerts_per_day - int(sent_today))
        delivery_line = (
            f"Today (UTC): **{int(sent_today)}/{normalized.max_alerts_per_day}** delivered • "
            f"**{remaining}** remaining before the daily cap."
        )
        cap_reached = int(sent_today) >= normalized.max_alerts_per_day

    lines = [
        delivery_line,
        "Process-local audit below resets when the bot restarts; durable delivery receipts still enforce dedupe and finite caps.",
        *telemetry.summary_lines(),
    ]
    if cap_reached:
        lines.append("⚠️ Your daily cap is currently reached; additional matching deals will be skipped until the next UTC day.")
    embed.add_field(
        name="📬 Delivery health",
        value="\n".join(lines)[:1024],
        inline=False,
    )


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
