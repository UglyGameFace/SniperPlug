from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    ATOM_SOURCE_KEY,
    AtomPromotionsClient,
    MovieTicketConfig,
    MovieTicketDrop,
    MovieTicketSourceState,
    MovieTicketStore,
    clean_text,
    parse_atom_promotions_html,
)


log = logging.getLogger("sniperplug.movie_tickets")

MOVIE_POLL_SECONDS = 60
MAX_LATEST_EMBEDS = 8
REQUIRED_CHANNEL_PERMS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
}


@dataclass(frozen=True, slots=True)
class MovieScanOutcome:
    modified: bool
    active_count: int
    delivered_count: int
    source_state: MovieTicketSourceState


class MovieTicketsCog(commands.GroupCog, name="movies"):
    """Official free movie-ticket drop alerts."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = MovieTicketStore(bot.db)
        self._session: aiohttp.ClientSession | None = None
        self._source_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        await self.store.ensure_schema()
        self._session = aiohttp.ClientSession()
        if not self.movie_drop_pump.is_running():
            self.movie_drop_pump.start()
        log.info("Movie ticket monitor loaded source=atom poll_seconds=%s", MOVIE_POLL_SECONDS)

    async def cog_unload(self) -> None:
        if self.movie_drop_pump.is_running():
            self.movie_drop_pump.cancel()
        session = self._session
        self._session = None
        if session is not None and not session.closed:
            await session.close()

    @app_commands.command(name="setup", description="Enable official free movie-ticket alerts in a selected channel.")
    @app_commands.describe(alert_channel="Channel where verified movie-ticket drops should post.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, alert_channel: discord.TextChannel) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        missing = self._missing_bot_permissions(interaction.guild, alert_channel)
        if missing:
            await interaction.followup.send(self._missing_permissions_message(alert_channel, missing), ephemeral=True)
            return

        config = MovieTicketConfig(
            guild_id=int(interaction.guild_id),
            alert_channel_id=int(alert_channel.id),
            enabled=True,
        )
        await self.store.save_config(config)

        try:
            outcome = await self._scan_official_source(target_guild_id=int(interaction.guild_id))
            scan_line = (
                f"Official Atom source checked. Active public free-ticket drops: **{outcome.active_count}**. "
                f"New alerts posted here: **{outcome.delivered_count}**."
            )
        except Exception as exc:
            log.exception("Movie ticket setup source refresh failed guild=%s", interaction.guild_id)
            scan_line = (
                "The channel is saved and automatic monitoring is enabled, but the first official-source check failed: "
                f"`{clean_text(exc)[:300]}`. SniperPlug will retry automatically."
            )

        await interaction.followup.send(
            f"🎬 Movie-ticket drops are **enabled** in {alert_channel.mention}.\n{scan_line}\n"
            "SniperPlug auto-posts only reusable **free-ticket** codes found on Atom's official promotions page. "
            "Private partner/account codes are not mislabeled as public codes.",
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Show movie-ticket destination, source health, and delivery totals.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        config = await self.store.get_config(int(interaction.guild_id))
        state = await self.store.get_source_state()
        active = await self.store.list_active_drops(limit=100)
        sent_count = await self.store.count_sent_for_guild(int(interaction.guild_id))

        embed = discord.Embed(title="🎬 Movie Ticket Monitor", color=discord.Color.gold())
        embed.add_field(name="Alerts", value="Enabled" if config.enabled else "Disabled", inline=True)
        embed.add_field(
            name="Channel",
            value=f"<#{config.alert_channel_id}>" if config.alert_channel_id else "Not configured",
            inline=True,
        )
        embed.add_field(name="Active public codes", value=str(len(active)), inline=True)
        embed.add_field(name="Delivered to this server", value=str(sent_count), inline=True)
        embed.add_field(name="Last successful check", value=_discord_time(state.last_success_at), inline=True)
        embed.add_field(name="Check interval", value=f"Every {MOVIE_POLL_SECONDS} seconds", inline=True)
        source_health = "Healthy" if state.last_success_at and not state.last_error else "Waiting for first success"
        if state.last_error:
            source_health = f"Error: `{clean_text(state.last_error)[:700]}`"
        embed.add_field(name="Official source", value=f"{source_health}\n[Atom Promotions Hub]({ATOM_PROMOTIONS_URL})", inline=False)
        embed.set_footer(text="Only public reusable free-ticket codes auto-post; targeted partner codes stay labeled private.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="latest", description="Refresh and show currently detected official free-ticket codes.")
    async def latest(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        refresh_error = ""
        try:
            await self._scan_official_source(target_guild_id=int(interaction.guild_id) if interaction.guild_id else None)
        except Exception as exc:
            refresh_error = clean_text(exc)[:500]
            log.exception("Manual movie latest refresh failed guild=%s", interaction.guild_id)

        drops = await self.store.list_active_drops(limit=MAX_LATEST_EMBEDS)
        if not drops:
            message = "No public reusable free-ticket codes are currently cached from Atom's official promotions page."
            if refresh_error:
                message += f"\nThe live refresh also failed: `{refresh_error}`"
            await interaction.followup.send(message, ephemeral=True)
            return

        content = f"Found **{len(drops)}** active official free-ticket drop(s). Claim quickly; Atom says offers can end when supplies run out."
        if refresh_error:
            content += f"\nLive refresh failed, so these are the last verified cached results: `{refresh_error}`"
        await interaction.followup.send(
            content=content,
            embeds=[build_movie_drop_embed(drop) for drop in drops],
            ephemeral=True,
        )

    @app_commands.command(name="scan", description="Check Atom's official promotions page now and deliver new drops.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def scan(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            outcome = await self._scan_official_source(target_guild_id=int(interaction.guild_id))
        except Exception as exc:
            log.exception("Manual movie scan failed guild=%s", interaction.guild_id)
            await interaction.followup.send(
                f"The official Atom source check failed safely: `{clean_text(exc)[:500]}`. No cached drops were deleted.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Official Atom scan complete. Page changed: **{'yes' if outcome.modified else 'no'}** • "
            f"Active public free-ticket codes: **{outcome.active_count}** • "
            f"New alerts delivered to this server: **{outcome.delivered_count}**.",
            ephemeral=True,
        )

    @app_commands.command(name="test-alert", description="Post a clearly labeled demo alert to the configured movie channel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test_alert(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        config = await self.store.get_config(int(interaction.guild_id))
        channel = await self._configured_channel(config)
        if channel is None:
            await interaction.followup.send("Run `/movies setup` first and select the destination channel.", ephemeral=True)
            return
        missing = self._missing_bot_permissions(interaction.guild, channel)
        if missing:
            await interaction.followup.send(self._missing_permissions_message(channel, missing), ephemeral=True)
            return

        demo = MovieTicketDrop(
            drop_id="movie-ticket-demo",
            source_key=ATOM_SOURCE_KEY,
            source_label="SniperPlug test only",
            title="Demo Movie — Not a Real Offer",
            code="SNIPERPLUGTEST",
            classification="public_reusable",
            ticket_limit=2,
            offer_url=ATOM_PROMOTIONS_URL,
            validity_text="Demo only — no ticket value.",
            restrictions=("This is a delivery test and cannot be redeemed.",),
            raw_text="",
        )
        message = await channel.send(
            embed=build_movie_drop_embed(demo, demo=True),
            view=movie_drop_link_view(demo.offer_url),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await interaction.followup.send(f"Demo alert posted successfully: {message.jump_url}", ephemeral=True)

    @app_commands.command(name="disable", description="Disable automatic movie-ticket alerts for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        config = await self.store.get_config(int(interaction.guild_id))
        config.enabled = False
        await self.store.save_config(config)
        await interaction.followup.send("Movie-ticket alerts are now **disabled** for this server.", ephemeral=True)

    @app_commands.command(name="sources", description="Show which official movie-ticket sources are connected or informational.")
    async def sources(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🎟️ Official Movie Ticket Sources",
            description="SniperPlug labels every source honestly so a private code is never presented as a universal public code.",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="✅ Automated now",
            value=(
                f"[Atom Promotions Hub]({ATOM_PROMOTIONS_URL}) — checked every {MOVIE_POLL_SECONDS} seconds with conditional requests. "
                "Extracts public reusable free-ticket codes, quantities, dates, and restrictions."
            ),
            inline=False,
        )
        embed.add_field(
            name="ℹ️ Official but not connected",
            value=(
                "Atom app push notifications, Atom email/SMS, official Atom social accounts, official movie/studio/distributor accounts, "
                "and partner apps such as Samsung Wallet. These can issue account-specific or unique codes and require a safe, consented source connection."
            ),
            inline=False,
        )
        embed.add_field(
            name="Not treated as original sources",
            value="Reddit and deal forums may help with backup discovery later, but they are repost sources and do not outrank the official publisher.",
            inline=False,
        )
        embed.set_footer(text="No Atom API or account login is used.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tasks.loop(seconds=MOVIE_POLL_SECONDS)
    async def movie_drop_pump(self) -> None:
        try:
            await self._scan_official_source()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Automatic official movie-ticket scan failed safely")
            try:
                await self.store.record_source_error(ATOM_SOURCE_KEY, str(exc))
            except Exception:
                log.exception("Could not persist movie-ticket source failure")

    @movie_drop_pump.before_loop
    async def before_movie_drop_pump(self) -> None:
        await self.bot.wait_until_ready()

    async def _scan_official_source(self, *, target_guild_id: int | None = None) -> MovieScanOutcome:
        async with self._source_lock:
            state = await self.store.get_source_state()
            try:
                client = AtomPromotionsClient(self._require_session())
                fetched = await client.fetch(state)
                modified = not fetched.not_modified
                if fetched.not_modified:
                    drops = await self.store.list_active_drops(limit=100)
                else:
                    parsed = parse_atom_promotions_html(fetched.html)
                    if not parsed.document_valid:
                        raise RuntimeError(
                            "The official Atom page structure could not be verified, so SniperPlug preserved the existing cache instead of guessing."
                        )
                    await self.store.replace_active_drops(ATOM_SOURCE_KEY, parsed.drops)
                    drops = list(parsed.drops)

                await self.store.record_source_success(
                    source_key=ATOM_SOURCE_KEY,
                    etag=fetched.etag or state.etag,
                    last_modified=fetched.last_modified or state.last_modified,
                    active_drop_count=len(drops),
                )
                delivered = await self._deliver_drops(drops, target_guild_id=target_guild_id)
                updated_state = await self.store.get_source_state()
                log.info(
                    "Official movie source checked modified=%s active=%s delivered=%s target_guild=%s",
                    modified,
                    len(drops),
                    delivered,
                    target_guild_id,
                )
                return MovieScanOutcome(modified, len(drops), delivered, updated_state)
            except Exception as exc:
                await self.store.record_source_error(ATOM_SOURCE_KEY, str(exc))
                raise

    async def _deliver_drops(self, drops: list[MovieTicketDrop] | tuple[MovieTicketDrop, ...], *, target_guild_id: int | None) -> int:
        if target_guild_id is None:
            configs = await self.store.list_enabled_configs()
        else:
            config = await self.store.get_config(int(target_guild_id))
            configs = [config] if config.enabled and config.alert_channel_id else []

        delivered = 0
        for config in configs:
            channel = await self._configured_channel(config)
            if channel is None:
                log.warning("Movie ticket destination missing guild=%s channel=%s", config.guild_id, config.alert_channel_id)
                continue
            guild = channel.guild
            missing = self._missing_bot_permissions(guild, channel)
            if missing:
                log.warning("Movie ticket destination lacks permissions guild=%s channel=%s missing=%s", guild.id, channel.id, missing)
                continue

            for drop in drops:
                if drop.classification != "public_reusable" or not drop.code:
                    continue
                reserved = await self.store.reserve_delivery(
                    guild_id=config.guild_id,
                    drop_id=drop.drop_id,
                    channel_id=channel.id,
                )
                if not reserved:
                    continue
                try:
                    message = await channel.send(
                        embed=build_movie_drop_embed(drop),
                        view=movie_drop_link_view(drop.offer_url),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    await self.store.mark_delivery_sent(
                        guild_id=config.guild_id,
                        drop_id=drop.drop_id,
                        channel_id=channel.id,
                        message_id=message.id,
                    )
                    delivered += 1
                except Exception as exc:
                    await self.store.mark_delivery_failed(
                        guild_id=config.guild_id,
                        drop_id=drop.drop_id,
                        error=str(exc),
                    )
                    log.exception(
                        "Movie ticket delivery failed guild=%s channel=%s drop=%s",
                        config.guild_id,
                        channel.id,
                        drop.drop_id,
                    )
        return delivered

    async def _configured_channel(self, config: MovieTicketConfig) -> discord.TextChannel | None:
        if not config.alert_channel_id:
            return None
        guild = self.bot.get_guild(int(config.guild_id))
        if guild is None:
            return None
        cached = guild.get_channel(int(config.alert_channel_id))
        if isinstance(cached, discord.TextChannel):
            return cached
        try:
            fetched = await guild.fetch_channel(int(config.alert_channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        return fetched if isinstance(fetched, discord.TextChannel) else None

    def _missing_bot_permissions(self, guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
        member = guild.me
        if member is None and self.bot.user is not None:
            member = guild.get_member(self.bot.user.id)
        if member is None:
            return list(REQUIRED_CHANNEL_PERMS.values())
        permissions = channel.permissions_for(member)
        return [label for attribute, label in REQUIRED_CHANNEL_PERMS.items() if not getattr(permissions, attribute, False)]

    @staticmethod
    def _missing_permissions_message(channel: discord.TextChannel, missing: list[str]) -> str:
        return (
            f"I cannot safely post movie-ticket alerts in {channel.mention}. Missing: **{', '.join(missing)}**. "
            "Grant those channel permissions and run `/movies setup` again."
        )

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("Movie-ticket HTTP session is not available yet.")
        return self._session


def build_movie_drop_embed(drop: MovieTicketDrop, *, demo: bool = False) -> discord.Embed:
    title = "🧪 MOVIE ALERT TEST" if demo else "🎟️ FREE ATOM TICKET DROP"
    color = discord.Color.blurple() if demo else discord.Color.green()
    embed = discord.Embed(
        title=title,
        description=f"**{drop.title}**\n{drop.value_label}",
        url=drop.offer_url,
        color=color,
        timestamp=datetime.now(UTC),
    )
    embed.add_field(name="Promo code", value=f"`{drop.code}`", inline=True)
    embed.add_field(name="Classification", value="Public reusable code" if not demo else "Demo only", inline=True)
    embed.add_field(name="Validity", value=clean_text(drop.validity_text)[:1024] or "See official terms.", inline=False)
    restriction_text = "\n".join(f"• {clean_text(item)}" for item in drop.restrictions[:5])
    if restriction_text:
        embed.add_field(name="Important restrictions", value=restriction_text[:1024], inline=False)
    embed.add_field(
        name="Claim",
        value="Open Atom, add the eligible ticket(s), and enter the code at checkout immediately. Supplies can run out before the listed end date.",
        inline=False,
    )
    embed.set_footer(text=f"Source: {drop.source_label} • SniperPlug verifies the source, not remaining redemption inventory")
    return embed


def movie_drop_link_view(url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Open official Atom offer", url=url or ATOM_PROMOTIONS_URL))
    return view


def _discord_time(value: str) -> str:
    if not value:
        return "Never"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return clean_text(value)[:100]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return f"<t:{int(parsed.timestamp())}:R>"
