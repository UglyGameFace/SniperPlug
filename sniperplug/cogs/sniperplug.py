from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.alert_renderer import DealActionView, build_deal_embed
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.monitor_control import MonitorMode, build_default_monitor_control_plane
from sniperplug.services.risk_flags import apply_risk_flags
from sniperplug.services.routing import (
    ALERT_ROUTES,
    DEFAULT_ROUTE,
    ROUTE_DESCRIPTIONS,
    choose_primary_route,
    is_valid_route,
    route_label,
)
from sniperplug.services.snipe_planner import build_default_snipe_batch


REQUIRED_CHANNEL_PERMS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}

ROUTE_CHOICES = [
    app_commands.Choice(name=route_label(route), value=route)
    for route in ALERT_ROUTES
]


def missing_channel_permissions(channel: discord.TextChannel, member: discord.Member) -> list[str]:
    perms = channel.permissions_for(member)
    missing: list[str] = []

    for attr, label in REQUIRED_CHANNEL_PERMS.items():
        if not getattr(perms, attr, False):
            missing.append(label)

    return missing


class SniperPlugCog(commands.GroupCog, name="sniperplug"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


    @app_commands.command(name="routes", description="Show SniperPlug alert channel routing for this server.")
    async def routes(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        stats = await self.bot.db.stats(interaction.guild_id)
        routes = stats.get("alert_routes", {})

        embed = discord.Embed(title="SniperPlug Routes", color=discord.Color.orange())
        for route in ALERT_ROUTES:
            channel_id = routes.get(route)
            channel_text = f"<#{channel_id}>" if channel_id else "Not set"
            embed.add_field(
                name=route_label(route),
                value=f"{channel_text}\n{ROUTE_DESCRIPTIONS[route]}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="provider_scan", description="Run a provider scan safely without posting public alerts.")
    @app_commands.describe(
        provider_key="Provider key, like bestbuy.",
        query="Optional search query for providers that support keyword scans.",
        category="Optional category key, like gpus or sneakers.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def provider_scan(
        self,
        interaction: discord.Interaction,
        provider_key: str,
        query: str | None = None,
        category: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        normalized_provider_key = provider_key.strip().lower()
        provider = provider_registry.get(normalized_provider_key)
        if provider is None:
            available = ", ".join(provider_registry.list_keys()) or "none"
            await interaction.followup.send(
                f"Provider `{normalized_provider_key}` is not registered. Available providers: `{available}`.",
                ephemeral=True,
            )
            return

        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send(
                f"`{provider.provider_key}` is not ready for live scans: {health.message}",
                ephemeral=True,
            )
            return

        request = ProviderScanRequest(
            source_key=provider.provider_key,
            query=query.strip() if query else None,
            category=category.strip().lower() if category else None,
            max_results=10,
            metadata={"requested_by": str(interaction.user.id)},
        )
        result = await provider.scan(request)

        embed = discord.Embed(
            title=f"🧪 {provider.display_name} Deal Scan Preview",
            description="Private preview only — no public alerts posted.",
            color=discord.Color.orange(),
        )

        if result.warnings:
            embed.add_field(
                name="⚠️ Notes",
                value="\n".join(f"• {warning}" for warning in result.warnings[:4]),
                inline=False,
            )

        if not result.candidates:
            embed.add_field(
                name="🔍 Results",
                value="No candidates returned.",
                inline=False,
            )
        else:
            for candidate in result.candidates[:5]:
                decision = evaluate_candidate(candidate)
                deal = decision.deal
                embed.add_field(
                    name=deal_preview_title(deal),
                    value=deal_preview_value(candidate, decision),
                    inline=False,
                )

        embed.set_footer(text="SniperPlug preview • verify checkout price, stock, shipping, and account eligibility before posting")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="snipe_plan", description="Show SniperPlug's current source-first scan priorities.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def snipe_plan(self, interaction: discord.Interaction) -> None:
        batch = build_default_snipe_batch(limit_sources=12, limit_categories=10)
        top_plans = batch.plans[:12]

        embed = discord.Embed(
            title="SniperPlug Snipe Plan",
            description="Source-first priorities for future providers. This command does not scan sites or make API calls.",
            color=discord.Color.orange(),
        )
        for plan in top_plans:
            query_preview = ", ".join(plan.queries[:4])
            embed.add_field(
                name=f"{plan.source_name} + {plan.category_label}",
                value=(
                    f"Priority: **{plan.priority}** • Cadence target: **{plan.cadence_seconds}s**\n"
                    f"Watch terms: `{query_preview}`\n"
                    f"{plan.reason}"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="monitor_plan", description="Show live monitor targets without scanning retailers.")
    @app_commands.describe(source_key="Optional source filter, like amazon, best_buy, walmart, msi_store, nike.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def monitor_plan(self, interaction: discord.Interaction, source_key: str | None = None) -> None:
        control_plane = build_default_monitor_control_plane(limit_targets=24)
        targets = control_plane.targets if not source_key else control_plane.by_source(source_key)

        embed = discord.Embed(
            title="SniperPlug Monitor Plan",
            description=(
                "Control-plane preview only. This does not scan retailers, call APIs, "
                "or post public alerts. Generated monitors default to Staff Review."
            ),
            color=discord.Color.orange(),
        )

        if not targets:
            embed.add_field(name="No monitor targets", value="No targets matched that filter.", inline=False)
        else:
            for target in targets[:12]:
                terms = ", ".join(target.watch_terms[:4]) or "None"
                embed.add_field(
                    name=f"{monitor_mode_label(target.mode)} • {target.source_name} + {target.category_label}",
                    value=(
                        f"Monitor: `{target.monitor_id}`\n"
                        f"Priority: **{target.priority}** • Cadence: **{target.cadence_seconds}s** • Cooldown: **{target.cooldown_seconds}s**\n"
                        f"Proof required: `{target.verification_required.value}`\n"
                        f"Route hint: `{target.route_hint or 'none'}`\n"
                        f"Watch terms: `{terms}`"
                    ),
                    inline=False,
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="test_alert", description="Post a realistic SniperPlug test deal alert.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test_alert(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        deal = NormalizedDeal(
            title="Apple iPhone 16 Pro Max, US Version, 256GB, Black Titanium - Unlocked (Renewed)",
            retailer="Amazon",
            product_url="https://www.amazon.com/",
            image_url="https://m.media-amazon.com/images/I/61giwQtR1qL._AC_SL1500_.jpg",
            current_price=179.99,
            typical_price=814.99,
            source="manual_test",
            marketplace="US",
            asin="B0DHJ896RY",
            seller_name="Example Merchant",
            fulfilled_by_amazon=False,
            fulfillment_type="Merchant Fulfilled",
            condition="Renewed",
            availability_message="Offer may not appear for every account.",
            verification_status="demo",
            is_price_verified=False,
            is_link_verified=False,
            is_image_verified=False,
            verification_notes=[
                "Test alert uses demo data only.",
                "Future provider alerts must set real verification flags.",
            ],
        )
        deal = apply_risk_flags(deal)
        route_decision = choose_primary_route(deal)

        channel_id = await self.bot.db.resolve_alert_channel(interaction.guild_id, route_decision.route)
        channel = await self._resolve_text_channel(interaction.guild, channel_id, interaction.channel)

        if channel is None:
            await interaction.followup.send("I could not find a valid text channel to post the alert.", ephemeral=True)
            return

        missing = self._missing_bot_perms(interaction.guild, channel)
        if missing:
            await interaction.followup.send(
                self._missing_permissions_message(channel, missing),
                ephemeral=True,
            )
            return

        await self.bot.db.upsert_deal(deal)

        embed = build_deal_embed(deal)
        embed.add_field(
            name="Route",
            value=f"{route_label(route_decision.route)} • {route_decision.reason}",
            inline=False,
        )
        view = DealActionView(self.bot.db, deal)

        try:
            await channel.send(embed=embed, view=view)
        except discord.Forbidden:
            await interaction.followup.send(
                self._missing_permissions_message(channel, list(REQUIRED_CHANNEL_PERMS.values())),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Posted a SniperPlug test alert in {channel.mention} using route **{route_label(route_decision.route)}**.",
            ephemeral=True,
        )

    @app_commands.command(name="scan_test", description="Preview demo source candidates. Set post_alerts true to post demo alerts.")
    @app_commands.describe(
        post_alerts="Post demo alerts. Defaults to private preview only.",
        test_channel="Optional test channel to receive all demo alerts instead of route channels.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def scan_test(
        self,
        interaction: discord.Interaction,
        post_alerts: bool = False,
        test_channel: discord.TextChannel | None = None,
    ) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        candidates = self._demo_source_candidates()
        decisions = [evaluate_candidate(candidate) for candidate in candidates]
        decisions = sorted(decisions, key=lambda decision: decision.anomaly.score, reverse=True)

        if not post_alerts:
            embed = discord.Embed(
                title="SniperPlug Scan Test Preview",
                description=(
                    "Private preview only. No public alerts were posted. "
                    "Run with `post_alerts:true` only when you intentionally want demo alerts. "
                    "Use `test_channel` to keep demo alerts out of real deal channels."
                ),
                color=discord.Color.orange(),
            )
            for decision in decisions[:5]:
                deal = decision.deal
                embed.add_field(
                    name=deal_preview_title(deal),
                    value=deal_preview_value_from_deal(deal, decision),
                    inline=False,
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if test_channel is not None:
            missing = self._missing_bot_perms(interaction.guild, test_channel)
            if missing:
                await interaction.followup.send(
                    self._missing_permissions_message(test_channel, missing),
                    ephemeral=True,
                )
                return

        posted = 0
        skipped: list[str] = []
        for decision in decisions[:5]:
            deal = decision.deal
            deal.verification_status = "demo"
            deal.is_price_verified = False
            deal.is_link_verified = False
            deal.is_image_verified = False
            deal.verification_notes = ["Demo source-candidate only. No retailer API or checkout call was made."]
            deal = apply_risk_flags(deal)

            if test_channel is not None:
                channel = test_channel
            else:
                channel_id = await self.bot.db.resolve_alert_channel(interaction.guild_id, decision.route.route)
                channel = await self._resolve_text_channel(interaction.guild, channel_id, interaction.channel)

            if channel is None:
                skipped.append(f"{deal.title}: no valid channel")
                continue

            missing = self._missing_bot_perms(interaction.guild, channel)
            if missing:
                skipped.append(f"{deal.title}: missing perms in #{channel.name}")
                continue

            await self.bot.db.upsert_deal(deal)
            embed = build_deal_embed(deal)
            embed.add_field(
                name="Sniper Score",
                value=(
                    f"**{decision.anomaly.score}/250** • {friendly_score_level(decision.anomaly.level)}\n"
                    + "\n".join(f"• {reason}" for reason in decision.reasons[:4])
                ),
                inline=False,
            )
            view = DealActionView(self.bot.db, deal)

            try:
                await channel.send(embed=embed, view=view)
                posted += 1
            except discord.Forbidden:
                skipped.append(f"{deal.title}: Discord 403 in #{channel.name}")

        destination = test_channel.mention if test_channel else "configured route channels"
        summary = f"Ran **{len(candidates)}** demo source candidates through SniperPlug. Posted **{posted}** demo alerts to {destination}."
        if skipped:
            summary += "\n\nSkipped:\n" + "\n".join(f"• {item}" for item in skipped[:5])
        await interaction.followup.send(summary, ephemeral=True)

    def _demo_source_candidates(self) -> list[SourceCandidate]:
        return [
            SourceCandidate(
                source_key="msi_store",
                retailer="MSI Store",
                title="GeForce RTX 5080 16G INSPIRE 3X OC Black Starter Kit",
                product_url="https://us-store.msi.com/",
                current_price=0.00,
                typical_price=9999.00,
                product_id="RTX5080-DEMO",
                product_id_type="sku",
                stock_status="In Stock",
                can_add_to_cart=True,
                signals=["Source page showed near-zero price", "Add-to-cart observed"],
            ),
            SourceCandidate(
                source_key="samsung",
                retailer="Samsung",
                title="Samsung 77 inch OLED TV brand-direct checkout price drop",
                product_url="https://www.samsung.com/us/",
                current_price=99.00,
                typical_price=2499.99,
                product_id="SAMSUNG-OLED-DEMO",
                product_id_type="sku",
                stock_status="Available",
                can_add_to_cart=True,
                is_checkout_price=True,
                signals=["Checkout price much lower than product value"],
            ),
            SourceCandidate(
                source_key="nike",
                retailer="Nike",
                title="Nike Air Jordan sneaker member price error",
                product_url="https://www.nike.com/",
                current_price=0.01,
                typical_price=180.00,
                product_id="NIKE-JORDAN-DEMO",
                product_id_type="sku",
                stock_status="Limited sizes",
                can_add_to_cart=True,
                is_member_only=True,
                signals=["Member-only pricing may vary"],
            ),
            SourceCandidate(
                source_key="autozone",
                retailer="AutoZone",
                title="Mobil 1 full synthetic motor oil case pack",
                product_url="https://www.autozone.com/",
                current_price=5.00,
                typical_price=72.00,
                product_id="OIL-CASE-DEMO",
                product_id_type="sku",
                stock_status="Available",
                can_add_to_cart=True,
                signals=["Bulk auto fluid price anomaly"],
            ),
            SourceCandidate(
                source_key="macys",
                retailer="Macy's",
                title="14k gold chain jewelry clearance price anomaly",
                product_url="https://www.macys.com/",
                current_price=49.99,
                typical_price=799.99,
                product_id="GOLD-CHAIN-DEMO",
                product_id_type="sku",
                stock_status="Available",
                can_add_to_cart=None,
                signals=["Jewelry price appears below expected value"],
            ),
        ]

    async def _resolve_text_channel(
        self,
        guild: discord.Guild,
        channel_id: int | None,
        fallback: discord.abc.GuildChannel | discord.InteractionChannel | None,
    ) -> discord.TextChannel | None:
        channel = None
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except discord.DiscordException:
                    channel = None

        if channel is None:
            channel = fallback

        return channel if isinstance(channel, discord.TextChannel) else None

    def _missing_bot_perms(self, guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
        bot_member = guild.me
        if bot_member is None and self.bot.user:
            bot_member = guild.get_member(self.bot.user.id)

        if bot_member is None:
            return []

        return missing_channel_permissions(channel, bot_member)

    def _missing_permissions_message(self, channel: discord.TextChannel, missing: list[str]) -> str:
        return (
            f"SniperPlug cannot post in {channel.mention} yet.\n\n"
            "**Missing channel permissions:**\n"
            + "\n".join(f"• {perm}" for perm in missing)
            + "\n\nGive the SniperPlug bot/role those permissions, then try again."
        )

    @set_channel.error
    @test_alert.error
    @scan_test.error
    @snipe_plan.error
    @monitor_plan.error
    @provider_scan.error
    async def admin_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need **Manage Server** permission to use this SniperPlug admin command."
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
            message = (
                "Discord blocked SniperPlug with **403 Missing Access**. "
                "Check the configured alert channel and give the bot View Channel, Send Messages, Embed Links, and Read Message History."
            )
        else:
            message = f"SniperPlug hit an error: `{error}`"

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def deal_preview_title(deal: NormalizedDeal) -> str:
    discount = discount_percent(deal.current_price, deal.typical_price)
    prefix = "🚨" if deal.is_possible_price_error else deal_heat_emoji(discount, deal.current_price)
    discount_text = f" {discount:.0f}% OFF •" if discount is not None else ""
    return f"{prefix}{discount_text} {deal.title}"[:256]


def deal_preview_value(candidate: SourceCandidate, decision) -> str:
    return deal_preview_value_from_deal(decision.deal, decision, candidate)


def deal_preview_value_from_deal(deal: NormalizedDeal, decision, candidate: SourceCandidate | None = None) -> str:
    price_line = format_price_line(deal.current_price, deal.typical_price)
    stock_line = candidate_stock_line(candidate, deal)
    score_line = f"⭐ **{friendly_score_level(decision.anomaly.level)}** • `{decision.anomaly.score}/250` • {route_label(decision.route.route)}"
    alert_line = "📣 Would alert: **Yes**" if decision.should_alert else "🛑 Would alert: **No**"
    link_line = f"🔗 [View deal]({deal.product_url})" if deal.product_url else "🔗 Link unavailable"

    notes = []
    if deal.alert_tags:
        notes.append(" ".join(deal.alert_tags[:3]))
    if decision.anomaly.reasons:
        notes.append("💡 " + decision.anomaly.reasons[0])

    value = f"{price_line}\n{score_line}\n{stock_line}\n{alert_line}\n{link_line}"
    if notes:
        value += "\n" + "\n".join(notes[:2])
    return value[:1024]


def format_price_line(current_price: float | None, typical_price: float | None) -> str:
    current = money(current_price)
    typical = money(typical_price)
    discount = discount_percent(current_price, typical_price)
    if discount is None:
        return f"💰 **{current}**"
    savings = (typical_price or 0) - (current_price or 0)
    return f"💰 **{current}** ~~{typical}~~ • **{discount:.0f}% OFF** • Save `{money(savings)}`"


def candidate_stock_line(candidate: SourceCandidate | None, deal: NormalizedDeal) -> str:
    stock = None
    add_to_cart = None
    if candidate:
        stock = candidate.stock_status
        add_to_cart = candidate.can_add_to_cart
    stock = stock or deal.availability_message

    pieces = []
    if stock:
        pieces.append(f"📦 {stock[:120]}")
    if add_to_cart is True:
        pieces.append("🛒 Add-to-cart seen")
    elif add_to_cart is False:
        pieces.append("🛒 Cart not confirmed")
    return " • ".join(pieces) if pieces else "📦 Stock not confirmed"


def discount_percent(current_price: float | None, typical_price: float | None) -> float | None:
    if current_price is None or not typical_price or typical_price <= 0:
        return None
    return max(0.0, (typical_price - current_price) / typical_price * 100)


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def deal_heat_emoji(discount: float | None, current_price: float | None) -> str:
    if current_price is not None and current_price <= 1:
        return "🚨"
    if discount is not None and discount >= 80:
        return "🔥"
    if discount is not None and discount >= 50:
        return "💎"
    if discount is not None and discount >= 30:
        return "✅"
    return "🔎"


def friendly_score_level(level: str) -> str:
    labels = {
        "nuclear": "Extreme",
        "urgent": "Urgent",
        "strong": "Strong",
        "watch": "Watch",
        "ignore": "Low",
    }
    return labels.get(level, level.title())


def provider_status_label(status: ProviderStatus) -> str:
    labels = {
        ProviderStatus.READY: "✅ Ready",
        ProviderStatus.STAGED: "🟡 Staged",
        ProviderStatus.DISABLED: "⏸️ Disabled",
        ProviderStatus.ERROR: "⚠️ Error",
    }
    return labels.get(status, "⚠️ Unknown")


def monitor_mode_label(mode: MonitorMode) -> str:
    labels = {
        MonitorMode.PREVIEW_ONLY: "🔎 Preview",
        MonitorMode.STAFF_REVIEW: "🛠️ Staff Review",
        MonitorMode.PUBLIC_ALLOWED: "📣 Public Allowed",
    }
    return labels.get(mode, mode.value)
