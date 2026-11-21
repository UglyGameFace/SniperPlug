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
    save_dm_deal_alert_preference,
)
from sniperplug.services.dm_delivery_policy import delete_dm_delivery_history
from sniperplug.services.dm_deal_routes import (
    personal_route_labels,
    split_dm_route_preferences,
)
from sniperplug.services.dm_delivery_telemetry import (
    UserDmDeliveryTelemetry,
    clear_user_dm_delivery_telemetry,
    get_user_dm_delivery_telemetry,
)
from sniperplug.services.dm_personal_categories import (
    category_label,
    compose_category_preferences,
    explicit_flip_min_profit_cents,
    flip_settings,
    normalize_personal_categories,
    split_category_preferences,
    update_flip_settings,
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
        description="Set your personal deal alerts in DMs.",
    )
    @app_commands.describe(
        action="Open the menu, inspect, test, pause, or update your personal alerts.",
        min_discount="Smallest discount you want, in percent.",
        max_price="Do not DM products above this current price, including price mistakes.",
        categories="Replace your picked categories: tech, gaming, PC, smart home.",
        product_interests="Add specific products: portable chargers, NeeDoh, Pokemon TCG, cologne, LEGO, and more.",
        remove_interests="Remove specific product interests from your personal DMs.",
        brands="Add brand filters such as Anker, Versace, Dior, YSL, Belkin, or LEGO.",
        remove_brands="Remove brands from your personal DM filter.",
        flip_min_profit="Price mistakes: only DM me when the was-to-now spread is at least this many dollars.",
        daily_cap="Daily DM limit: 1-100, or type unlimited for no cap.",
    )
    @app_commands.choices(action=ACTION_CHOICES)
    async def dm_deals(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        min_discount: app_commands.Range[int, 0, 95] | None = None,
        max_price: app_commands.Range[float, 0.01, 100000.0] | None = None,
        categories: app_commands.Range[str, 1, 300] | None = None,
        product_interests: app_commands.Range[str, 1, 400] | None = None,
        remove_interests: app_commands.Range[str, 1, 400] | None = None,
        brands: app_commands.Range[str, 1, 400] | None = None,
        remove_brands: app_commands.Range[str, 1, 400] | None = None,
        flip_min_profit: app_commands.Range[float, 10.0, 100000.0] | None = None,
        daily_cap: app_commands.Range[str, 1, 12] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = int(interaction.user.id)
        selected_action = str(action.value)

        # Typed filters only apply to the enable/update action. If another
        # action was picked with filters typed, say so instead of dropping
        # them silently.
        _typed_options: dict[str, object | None] = {
            "min_discount": min_discount,
            "max_price": max_price,
            "categories": categories,
            "product_interests": product_interests,
            "remove_interests": remove_interests,
            "brands": brands,
            "remove_brands": remove_brands,
            "flip_min_profit": flip_min_profit,
            "daily_cap": daily_cap,
        }
        _ignored_options = sorted(
            name for name, value in _typed_options.items() if value is not None
        )
        _ignored_note = (
            "Note: "
            + ", ".join(f"`{name}`" for name in _ignored_options)
            + " only applies when action is Enable or update — it was ignored for this action."
            if selected_action != "enable" and _ignored_options
            else ""
        )

        def _with_ignored_note(embed: discord.Embed) -> discord.Embed:
            if _ignored_note:
                embed.add_field(name="Ignored options", value=_ignored_note, inline=False)
            return embed

        def _with_ignored_text(text: str) -> str:
            return text + ("\n\n" + _ignored_note if _ignored_note else "")

        if selected_action == "delete":
            await delete_dm_deal_alert_preference(self.bot.db, user_id)
            await delete_dm_delivery_history(self.bot.db, user_id=user_id)
            clear_user_dm_delivery_telemetry(user_id)
            await interaction.followup.send(
                _with_ignored_text("🗑️ Your SniperPlug DM alert settings and delivery receipts were deleted."),
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
                embed=_with_ignored_note(view.build_embed()),
                view=view,
                ephemeral=True,
            )
            return

        if selected_action == "interests":
            await interaction.followup.send(
                embed=_with_ignored_note(build_product_interest_catalog_embed(preference)),
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
                embed=_with_ignored_note(embed),
                ephemeral=True,
            )
            return

        if selected_action == "disable":
            saved = await save_dm_deal_alert_preference(
                self.bot.db,
                replace(preference, enabled=False),
            )
            await interaction.followup.send(
                embed=_with_ignored_note(
                    build_dm_settings_embed(
                        saved,
                        title="⏸️ Personal deal DMs paused",
                    )
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
                    "I could not DM you. Allow direct messages from server members for a server we share, then run `/walmart my-alerts action:Send a test DM` again.",
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
                _with_ignored_text(
                    "✅ Test DM delivered. Your alerts are still "
                    + ("enabled." if preference.enabled else "paused until you choose Enable or update alerts.")
                ),
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
                f"these specific product interests: {preview}. Run `/walmart my-alerts "
                "action:Show specific product catalog` to see accepted names.",
                ephemeral=True,
            )
            return

        updated_categories = preference.categories
        if categories is not None:
            current_flip_enabled, current_flip_profit = flip_settings(
                updated_categories
            )
            updated_categories = compose_category_preferences(
                normalize_personal_categories(categories),
                flip_enabled=current_flip_enabled,
                flip_min_profit_cents=current_flip_profit,
                product_tokens=updated_categories,
                routes=split_dm_route_preferences(updated_categories),
            )
        if flip_min_profit is not None:
            updated_categories = update_flip_settings(
                updated_categories,
                minimum_profit_cents=int(round(float(flip_min_profit) * 100)),
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
            categories=updated_categories,
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
        # Enabling with nothing typed while already enabled changes nothing;
        # say so instead of reporting a fresh enable.
        enable_title = (
            "✅ Personal exact-deal DMs already enabled — nothing changed"
            if preference.enabled and not _ignored_options
            else "✅ Personal exact-deal DMs enabled"
        )
        await interaction.followup.send(
            embed=build_dm_settings_embed(
                saved,
                title=enable_title,
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
    selected_routes = split_dm_route_preferences(normalized.categories)
    route_names = dict(personal_route_labels())
    product_interests, product_brands = split_product_preferences(
        normalized.categories
    )
    selected_categories = split_category_preferences(normalized.categories)
    flip_floor_cents = explicit_flip_min_profit_cents(normalized.categories)

    embed = discord.Embed(
        title=title,
        description=normalized.summary_sentence(),
        color=discord.Color.green() if normalized.enabled else discord.Color.orange(),
    )

    picks: list[str] = []
    if selected_routes:
        picks.append(
            "Kinds of deals: "
            + ", ".join(route_names[route] for route in selected_routes)
        )
    if product_interests:
        picks.append(
            "Specific products: "
            + ", ".join(
                product_interest_label(key) for key in product_interests
            )
        )
    if product_brands:
        picks.append(
            "Brands: "
            + ", ".join(brand_label(slug) for slug in product_brands)
        )
    if selected_categories:
        picks.append(
            "Categories: "
            + ", ".join(
                category_label(category) for category in selected_categories
            )
        )
    if picks:
        embed.add_field(
            name="🔔 What DMs you",
            value="\n".join(picks)[:1024],
            inline=False,
        )

    price_text = (
        f"${normalized.max_price_cents / 100:,.2f}"
        if normalized.max_price_cents is not None
        else "no limit"
    )
    quality = (
        f"**{normalized.min_discount}%+** off • max price **{price_text}** • "
        f"daily limit **{daily_cap_label(normalized.max_alerts_per_day)}**"
    )
    if flip_floor_cents > 0 and "price_glitch" in selected_routes:
        quality += (
            f" • price mistakes worth "
            f"**${flip_floor_cents / 100:,.2f}+**"
        )
    embed.add_field(
        name="🎚️ How good, how many",
        value=quality,
        inline=False,
    )
    embed.add_field(
        name="Open the complete menu",
        value=(
            "Run `/walmart my-alerts action:Open personalization menu` to pick "
            "kinds of deals, products, brands and categories with buttons."
        ),
        inline=False,
    )
    embed.add_field(
        name="Built-in safety",
        value=(
            "Only buyable deals whose current and was prices were both checked "
            "can DM you. A deal DMs you when it matches anything you picked — "
            "kinds of deals, products, brands, categories, or your wishlist — "
            "and your discount, max price and daily limit always apply."
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
            "These are intentionally narrower than categories. Add them with `/walmart my-alerts "
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
        text="Product picks are one way to get a DM. Kinds of deals, categories and your wishlist are the others — a match on any of them counts."
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
        text="Use /walmart my-alerts action:Open personalization menu or action:Pause alerts at any time."
    )
    return embed
