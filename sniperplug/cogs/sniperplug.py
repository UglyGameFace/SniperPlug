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
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.risk_flags import apply_risk_flags
from sniperplug.services.routing import (
    ALERT_ROUTES,
    ROUTE_DESCRIPTIONS,
    choose_primary_route,
    route_label,
)
from sniperplug.services.walmart_missed_deal_trace import trace_walmart_item


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


class SniperPlugCog(commands.Cog):
    """Handlers that used to be the /sniperplug group (dissolved in PR 2).

    No slash commands live here anymore: routes is a page of /staff
    dashboard, provider_scan runs from the /menu server screen, and
    test_alert is /staff test-alert. The three static demo commands
    (snipe_plan, monitor_plan, scan_test) were deleted outright — they
    displayed invented data. deal_trace stays a plain method for the
    Walmart staff menu, as before.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # No slash decorator: this is the Routes page of /staff dashboard.
    async def routes(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        stats = await self.bot.db.stats(interaction.guild_id)
        routes = stats.get("alert_routes", {})
        public_config = await get_public_alert_config(
            self.bot.db, interaction.guild_id
        )
        public_channel_id = public_config.get("channel_id")

        embed = discord.Embed(title="SniperPlug Routes", color=discord.Color.orange())
        # Truth-in-labeling: automatic public posts are NOT routed. The
        # canonical pipeline (guild_public_alert_settings + bulk fanout)
        # sends every automatic post to the single public alert channel;
        # it never consults this legacy per-route map. The map's only
        # live readers are the staff test alert (resolve_alert_channel)
        # and setup repair, which keeps the default route as a saved
        # backup channel. Say so instead of showing "Not set" for every
        # route while the server is in fact posting.
        if public_channel_id:
            destination = (
                "Automatic deal posts all go to this server's public "
                f"alert channel, <#{public_channel_id}> — they are not "
                "split by route."
            )
        else:
            destination = (
                "Automatic deal posts are not set up for this server "
                "yet — no public alert channel is saved."
            )
        embed.description = (
            f"{destination}\n\n"
            "The channels below are legacy per-route overrides. "
            "Automatic posts never use them: a route channel is only "
            "used when staff send a test alert, and the Default Deals "
            "channel doubles as a saved backup the setup check can "
            "restore the public channel from. 'No separate channel set' "
            "means that route has no override — it does not mean deals "
            "have nowhere to post."
        )
        for route in ALERT_ROUTES:
            channel_id = routes.get(route)
            channel_text = (
                f"<#{channel_id}>" if channel_id else "No separate channel set"
            )
            embed.add_field(
                name=route_label(route),
                value=f"{channel_text}\n{ROUTE_DESCRIPTIONS[route]}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # No slash decorator: this runs from the /menu server screen
    # (Provider scan), which enforces the Manage Server gate itself.
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

    # No slash decorator: this is the Walmart staff menu's Trace an item.
    # It stays a plain method so the Walmart menu can delegate to it; the
    # menu enforces the Manage Server gate itself before delegating.
    async def deal_trace(self, interaction: discord.Interaction, item_id: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "This diagnostic requires a Discord server.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await trace_walmart_item(
            getattr(self.bot, "db", None),
            item_id=item_id,
            guild_id=interaction.guild_id,
        )
        embed = discord.Embed(
            title="SniperPlug • exact Walmart item trace",
            description=(
                f"Item: `{result.item_id[:24]}`\n"
                f"Result: **{result.outcome.replace('_', ' ')}**\n"
                f"{result.detail}"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Exact queue",
            value=(
                f"Status: `{result.queue_status or 'not retained'}`\n"
                f"First seen: {result.first_seen_at or 'unavailable'}\n"
                f"Last seen: {result.last_seen_at or 'unavailable'}\n"
                f"Verified: {result.verified_at or 'unavailable'}\n"
                f"Next attempt: {result.next_attempt_at or 'unavailable'}"
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Fanout / delivery",
            value=(
                f"Event seen: {result.event_seen_at or 'not in sample'}\n"
                f"Processed: {result.event_processed_at or 'unknown'}\n"
                f"Guild receipt: {result.post_status or 'not located'}\n"
                f"Posted: {result.posted_at or 'not located'}\n"
                f"Error class: {result.last_error_category or result.query_error or 'none'}"
            )[:1024],
            inline=False,
        )
        def duration(value: int | None) -> str:
            return f"{value:,}s" if value is not None else "unverified"

        embed.add_field(
            name="Measured latency",
            value=(
                f"Discovery → verified: {duration(result.discovery_to_verification_seconds)}\n"
                f"Discovery → post: {duration(result.discovery_to_post_seconds)}\n"
                f"Verified → post: {duration(result.verification_to_post_seconds)}"
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"Read-only • last {result.sampled_events} event rows searched; "
                "absence is not proof the retailer never listed it"
            )
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # No slash decorator: this is /staff test-alert; the group command
    # carries the Manage Server gate.
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
