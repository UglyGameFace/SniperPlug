from __future__ import annotations

"""Single-app eBay research and consent. No legacy watcher or automatic sender."""

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.ebay_browse_api import EbayApiError, EbayBrowse, EbaySettings
from sniperplug.services.ebay_promotion_consent import (
    DISCLOSURE, guild_consent, may_promote, preview_text, safe_affiliate_link,
    save_guild_consent, save_user_consent, user_consented,
)


class EbayConsentView(discord.ui.View):
    """Private real subscription/unsubscription controls for EPN review."""

    def __init__(self, *, bot: commands.Bot, user_id: int):
        super().__init__(timeout=900)
        self.bot = bot
        self.user_id = user_id

    async def _apply(self, interaction: discord.Interaction, enabled: bool) -> None:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "Only the subscriber can change this preference.", ephemeral=True,
            )
            return
        await save_user_consent(self.bot.db, self.user_id, enabled)
        await interaction.response.send_message(
            (
                "eBay promotional DMs: **opted in**. Messages are NOT being "
                "sent without written eBay approval."
                if enabled else
                "eBay promotional DMs: **unsubscribed**. Your consent was removed."
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="Opt in to eBay DMs", style=discord.ButtonStyle.success)
    async def opt_in(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._apply(interaction, True)

    @discord.ui.button(label="Unsubscribe / Opt out", style=discord.ButtonStyle.danger)
    async def opt_out(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._apply(interaction, False)


class EbayResearchCog(commands.GroupCog, group_name="ebay", group_description="eBay market research and notification consent"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.settings = EbaySettings.from_env()
        self.browse = EbayBrowse(self.settings)

    @app_commands.command(name="status", description="Check eBay integration and your opt-in status.")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        opted_in = await user_consented(self.bot.db, int(interaction.user.id))
        guild = (
            await guild_consent(self.bot.db, int(interaction.guild_id))
            if interaction.guild_id else None
        )
        lines = [
            "**SniperPlug eBay readiness**",
            "Browse research: **" +
            ("enabled" if self.settings.research_enabled and self.settings.credentials_ready else "not configured") + "**",
            "Written EPN messaging approval: **" +
            ("configured" if self.settings.messaging_approved else "NOT CONFIGURED") + "**",
            "Affiliate messaging: **" + ("eligible" if self.settings.affiliate_ready else "OFF") + "**",
            "Your eBay DM subscription: **" + ("ON" if opted_in else "OFF") + "**",
            "Automated eBay promotional sender: **NOT DEPLOYED**",
            "Active listing asking prices are not completed sales or proven resale profit.",
        ]
        if guild:
            lines.append("Server channel opt-in: **" + ("ON" if guild.enabled else "OFF") + "**")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="consent", description="Subscribe or unsubscribe to future eBay promotional DMs.")
    @app_commands.describe(enabled="True to opt in, false to opt out. Omit to view controls.")
    async def consent(self, interaction: discord.Interaction, enabled: bool | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        if enabled is not None:
            await save_user_consent(self.bot.db, int(interaction.user.id), bool(enabled))
        subscribed = await user_consented(self.bot.db, int(interaction.user.id))
        await interaction.followup.send(
            "**eBay promotional DMs:** " + ("OPTED IN" if subscribed else "NOT SUBSCRIBED") +
            "\nYou control this setting. Separate from Walmart notifications. "
            "No eBay promotional messages are active before EPN approval. "
            "Use Unsubscribe to remove consent immediately.",
            view=EbayConsentView(bot=self.bot, user_id=int(interaction.user.id)),
            ephemeral=True,
        )

    @app_commands.command(name="server", description="Choose whether this Discord server consents to eBay promotions.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(enabled="True to record channel opt-in; false to clear it.")
    async def server(self, interaction: discord.Interaction, enabled: bool) -> None:
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild_id or not interaction.channel_id:
            await interaction.followup.send("Use this in the intended Discord server channel.", ephemeral=True)
            return
        await save_guild_consent(
            self.bot.db,
            guild_id=int(interaction.guild_id),
            actor_id=int(interaction.user.id),
            channel_id=int(interaction.channel_id) if enabled else None,
            enabled=enabled,
        )
        await interaction.followup.send(
            "Recorded eBay channel consent: **" + ("ENABLED" if enabled else "DISABLED") +
            "**. No automatic promotional sender exists; this cannot bypass EPN approval.",
            ephemeral=True,
        )

    @app_commands.command(name="research", description="Research active fixed-price eBay listings, not sold prices.")
    @app_commands.describe(
        query="Item name, model, or category to search (leave empty if entering a GTIN).",
        gtin="Exact 8/12/13/14-digit barcode (leave query empty).",
    )
    @app_commands.checks.cooldown(2, 60.0, key=lambda interaction: interaction.user.id)
    async def research(
        self,
        interaction: discord.Interaction,
        query: app_commands.Range[str, 2, 100] | None = None,
        gtin: app_commands.Range[str, 8, 14] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            listings = await self.browse.search(query=query or "", gtin=gtin or "")
        except EbayApiError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        opted_in = await user_consented(self.bot.db, int(interaction.user.id))
        embed = discord.Embed(
            title="eBay active listing research",
            description=(
                "Search: **" + discord.utils.escape_markdown(query or gtin or "") +
                "**\nThese are ASKING prices, not verified completed sales or resale profits."
            ),
            color=discord.Color.blue(),
        )
        for listing in listings[:5]:
            delivered = (
                "USD " + f"{listing.delivered_cents / 100:,.2f}" + " incl. shipping"
                if listing.delivered_cents is not None
                else "shipping total unknown"
            )
            affiliate = safe_affiliate_link(
                self.settings, listing, opted_in=opted_in,
            )
            link = affiliate or listing.asking_url
            embed.add_field(
                name=discord.utils.escape_markdown(listing.title)[:150],
                value=(
                    "Asking: USD " + f"{listing.price_cents / 100:,.2f}" +
                    " | Delivered: " + delivered + "\n" +
                    "Condition: " + discord.utils.escape_markdown(listing.condition)[:80] +
                    "\n[View eBay item](" + link + ")"
                )[:1024],
                inline=False,
            )
        if not listings:
            embed.add_field(
                name="No results",
                value="No usable fixed-price USD listings found. This does not prove zero buyer demand.",
                inline=False,
            )
        embed.set_footer(
            text=(
                DISCLOSURE if may_promote(self.settings, opted_in=opted_in)
                else "Non-affiliate, user-requested research. No promotional DM sent."
            )
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="review_sample", description="Privately preview an actual eBay listing and opt-out controls.")
    @app_commands.describe(query="Search term used to fetch a real product for review.")
    @app_commands.checks.cooldown(2, 60.0, key=lambda interaction: interaction.user.id)
    async def review_sample(self, interaction: discord.Interaction, query: app_commands.Range[str, 2, 100]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            listings = await self.browse.search(query=query, limit=1)
        except EbayApiError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        if not listings:
            await interaction.followup.send("No live eBay item returned for this sample.", ephemeral=True)
            return
        opted_in = await user_consented(self.bot.db, int(interaction.user.id))
        await interaction.followup.send(
            "**PRIVATE REVIEW SAMPLE. Not a sent promotion.**\n" +
            preview_text(listings[0], approved=False) +
            "\n" + DISCLOSURE +
            "\nSubscription: " + ("opted in" if opted_in else "not subscribed") +
            "\nUnsubscribe with **/ebay consent** or the button below.",
            view=EbayConsentView(bot=self.bot, user_id=int(interaction.user.id)),
            ephemeral=True,
        )
