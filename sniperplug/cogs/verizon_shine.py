from __future__ import annotations

import logging
import os
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from sniperplug.services.verizon_shine import (
    VerizonShineConfig,
    VerizonShineReward,
    VerizonShineStore,
    build_summary_lines,
    human_time,
    is_relevant_notification,
    parse_rewards_from_text,
    reward_from_due_row,
    should_alert,
    status_label,
)

log = logging.getLogger("sniperplug.verizon_shine")

REQUIRED_CHANNEL_PERMS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}


class VerizonShineCog(commands.GroupCog, name="verizon"):
    """Read-only Verizon Shine / myAccess reward alerts for SniperPlug."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = VerizonShineStore(bot.db)
        self._relay_runner: Any | None = None
        self._relay_site: Any | None = None
        self._relay_started = False
        self._ready_guild_sync_done = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.store.ensure_schema()
        if not self.reminder_pump.is_running():
            self.reminder_pump.start()
        await self._start_optional_relay()
        await self._sync_all_joined_guilds_once(reason="ready")
        guilds = sorted((f"{guild.name}({guild.id})" for guild in self.bot.guilds), key=str.lower)
        log.info(
            "Verizon Shine alert module ready: reminders=true relay=%s visible_guilds=%s [%s]",
            self._relay_started,
            len(guilds),
            ", ".join(guilds[:25]),
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("SniperPlug joined guild %s (%s) members=%s", guild.name, guild.id, guild.member_count)
        await self._sync_guild_commands(guild, reason="guild_join")

    async def cog_unload(self) -> None:
        if self.reminder_pump.is_running():
            self.reminder_pump.cancel()
        if self._relay_runner is not None:
            try:
                await self._relay_runner.cleanup()
            except Exception:
                log.exception("Failed to clean up Verizon Shine relay")

    @app_commands.command(name="setup", description="Configure Verizon Shine reward alerts for this server.")
    @app_commands.describe(
        alert_channel="Channel where Verizon Shine alerts should post.",
        enabled="Turn Verizon Shine alerts on or off.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        alert_channel: discord.TextChannel,
        enabled: bool = True,
    ) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        missing = self._missing_bot_perms(interaction.guild, alert_channel)
        if missing:
            await interaction.followup.send(self._missing_permissions_message(alert_channel, missing), ephemeral=True)
            return

        config = await self.store.get_config(interaction.guild_id)
        config.alert_channel_id = alert_channel.id
        config.enabled = enabled
        await self.store.save_config(config)

        await interaction.followup.send(
            f"Verizon Shine alerts are now **{'enabled' if enabled else 'disabled'}** in {alert_channel.mention}.\n"
            "Safe mode: read-only alerts only. No Verizon login, no auto-claiming, no CAPTCHA bypass.",
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Show Verizon Shine alert setup and recent reward count.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        config = await self.store.get_config(interaction.guild_id)
        rewards = await self.store.list_rewards(interaction.guild_id, limit=5)
        embed = discord.Embed(title="Verizon Shine Alert Status", color=discord.Color.gold())
        embed.add_field(name="Enabled", value="Yes" if config.enabled else "No", inline=True)
        embed.add_field(name="Alert channel", value=f"<#{config.alert_channel_id}>" if config.alert_channel_id else "Not set", inline=True)
        embed.add_field(name="Reminders", value="On" if config.reminders_enabled else "Off", inline=True)
        embed.add_field(name="Reminder offsets", value=", ".join(f"{m}m" for m in config.reminder_offsets), inline=True)
        embed.add_field(name="Priority keywords", value=", ".join(config.priority_keywords[:15]) or "None", inline=False)
        if rewards:
            embed.add_field(
                name="Recent rewards",
                value="\n".join(f"• **{reward.title}** — {status_label(reward.status)}" for reward in rewards),
                inline=False,
            )
        embed.set_footer(text="SniperPlug Verizon Shine module • read-only alerting")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="scan", description="Scan pasted Verizon Shine/myAccess reward text and post only if it is new or changed.")
    @app_commands.describe(text="Paste reward text or a screenshot summary. Phase 1 does not OCR screenshots.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def scan(self, interaction: discord.Interaction, text: str) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        result = await self._ingest_text(
            interaction.guild,
            interaction.guild_id,
            text,
            source="manual",
            fallback_channel=interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None,
        )

        await interaction.followup.send(result, ephemeral=True)

    @app_commands.command(name="test-alert", description="Post a safe demo Verizon Shine alert.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test_alert(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        sample = (
            "Verizon Shine Daily Drop\n"
            "$25 gift card reward\n"
            "Available in 30 minutes\n"
            "Open My Verizon app > Me > Shine"
        )
        result = await self._ingest_text(
            interaction.guild,
            interaction.guild_id,
            sample,
            source="manual_test",
            fallback_channel=interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None,
            force_post=True,
        )
        await interaction.followup.send(result, ephemeral=True)

    @app_commands.command(name="add-keyword", description="Add a Verizon Shine priority keyword.")
    @app_commands.describe(keyword="Keyword or phrase that should make rewards high priority.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_keyword(self, interaction: discord.Interaction, keyword: str) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        _, added = await self.store.add_keyword(interaction.guild_id, keyword)
        if added:
            await interaction.followup.send(f"Added priority keyword: `{keyword.strip()}`.", ephemeral=True)
        else:
            await interaction.followup.send(f"`{keyword.strip()}` is already in the priority list.", ephemeral=True)

    @app_commands.command(name="remove-keyword", description="Remove a Verizon Shine priority keyword.")
    @app_commands.describe(keyword="Keyword or phrase to remove.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_keyword(self, interaction: discord.Interaction, keyword: str) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        _, removed = await self.store.remove_keyword(interaction.guild_id, keyword)
        if removed:
            await interaction.followup.send(f"Removed priority keyword: `{keyword.strip()}`.", ephemeral=True)
        else:
            await interaction.followup.send(f"`{keyword.strip()}` was not in the priority list.", ephemeral=True)

    @app_commands.command(name="list-keywords", description="List Verizon Shine priority keywords.")
    async def list_keywords(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        config = await self.store.get_config(interaction.guild_id)
        visible_keywords = config.priority_keywords[:50]
        extra = max(0, len(config.priority_keywords) - len(visible_keywords))
        body = "**Verizon Shine priority keywords:**\n" + "\n".join(f"• `{keyword}`" for keyword in visible_keywords)
        if extra:
            body += f"\n…and {extra} more."
        await interaction.followup.send(body, ephemeral=True)

    @app_commands.command(name="reminders", description="Turn Verizon Shine countdown reminders on or off.")
    @app_commands.describe(enabled="Whether reminder alerts should be sent before available_at.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reminders(self, interaction: discord.Interaction, enabled: bool) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        config = await self.store.get_config(interaction.guild_id)
        config.reminders_enabled = enabled
        await self.store.save_config(config)
        await interaction.followup.send(f"Verizon Shine reminders are now **{'on' if enabled else 'off'}**.", ephemeral=True)

    @app_commands.command(name="digest", description="Show recent Verizon Shine rewards SniperPlug has seen.")
    async def digest(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        rewards = await self.store.list_rewards(interaction.guild_id, limit=10)
        embed = discord.Embed(title="Verizon Shine Digest", color=discord.Color.gold())
        if not rewards:
            embed.description = "No Verizon Shine rewards have been saved yet."
        else:
            for reward in rewards:
                embed.add_field(
                    name=reward.title[:256],
                    value="\n".join(build_summary_lines(reward)),
                    inline=False,
                )
        embed.set_footer(text="Digest is based on saved alerts/manual scans only.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tasks.loop(minutes=1)
    async def reminder_pump(self) -> None:
        try:
            due = await self.store.due_reminders(limit=25)
        except Exception:
            log.exception("Verizon Shine reminder check failed")
            return

        for row in due:
            guild = self.bot.get_guild(int(row["guild_id"]))
            if guild is None:
                await self.store.mark_reminder_sent(int(row["guild_id"]), str(row["reward_id"]), int(row["offset_minutes"]))
                continue
            config = await self.store.get_config(guild.id)
            if not config.enabled or not config.reminders_enabled:
                continue
            reward = reward_from_due_row(row)
            channel = await self._resolve_alert_channel(guild, config)
            if channel is None:
                continue

            embed = self._reward_embed(reward, title_prefix=f"⏰ {row['offset_minutes']}m Reminder")
            try:
                await channel.send(embed=embed)
                await self.store.mark_reminder_sent(guild.id, reward.reward_id, int(row["offset_minutes"]))
            except discord.Forbidden:
                log.warning("Missing permission to send Verizon Shine reminder guild=%s channel=%s", guild.id, getattr(channel, "id", None))
            except Exception:
                log.exception("Failed to send Verizon Shine reminder guild=%s reward=%s", guild.id, reward.reward_id)

    @reminder_pump.before_loop
    async def before_reminder_pump(self) -> None:
        await self.bot.wait_until_ready()

    async def _sync_all_joined_guilds_once(self, *, reason: str) -> None:
        if self._ready_guild_sync_done:
            return
        self._ready_guild_sync_done = True
        if not self._env_enabled("SYNC_JOINED_GUILD_COMMANDS", default=True):
            log.info("Joined guild command sync disabled by SYNC_JOINED_GUILD_COMMANDS=false")
            return
        if getattr(getattr(self.bot, "settings", None), "sync_global_commands", False):
            log.info("Global command sync is enabled; skipping joined guild command sync sweep")
            return
        for guild in list(self.bot.guilds):
            await self._sync_guild_commands(guild, reason=reason)

    async def _sync_guild_commands(self, guild: discord.Guild, *, reason: str) -> None:
        if not self._env_enabled("SYNC_JOINED_GUILD_COMMANDS", default=True):
            return
        if getattr(getattr(self.bot, "settings", None), "sync_global_commands", False):
            return
        try:
            target = discord.Object(id=guild.id)
            self.bot.tree.copy_global_to(guild=target)
            synced = await self.bot.tree.sync(guild=target)
            log.info("Synced %s SniperPlug slash commands to guild %s (%s) reason=%s", len(synced), guild.name, guild.id, reason)
        except discord.Forbidden:
            log.warning("Could not sync SniperPlug commands to guild %s (%s): missing access", guild.name, guild.id)
        except Exception:
            log.exception("Failed to sync SniperPlug commands to guild %s (%s)", guild.name, guild.id)

    async def _ingest_text(
        self,
        guild: discord.Guild,
        guild_id: int,
        text: str,
        *,
        source: str,
        fallback_channel: discord.TextChannel | None = None,
        force_post: bool = False,
    ) -> str:
        config = await self.store.get_config(guild_id)
        rewards = parse_rewards_from_text(guild_id, text, source=source, keywords=config.priority_keywords)
        if not rewards:
            return "No Verizon Shine rewards detected from that text."

        channel = await self._resolve_alert_channel(guild, config, fallback=fallback_channel)
        posted = 0
        saved = 0
        suppressed = 0
        reminders = 0
        notes: list[str] = []

        for reward in rewards:
            existing = await self.store.get_reward(guild_id, reward.reward_id)
            if existing:
                reward.first_seen_at = existing.first_seen_at

            alert, reason = should_alert(existing, reward, config)
            if force_post:
                alert = True
                reason = "forced test alert"

            await self.store.upsert_reward(reward)
            saved += 1

            if reward.available_at and config.reminders_enabled:
                reminders += await self.store.save_reminders(reward, config.reminder_offsets)

            if not config.enabled and not force_post:
                notes.append(f"Saved `{reward.title}` but alerts are disabled.")
                continue

            if alert:
                if channel is None:
                    notes.append(f"Saved `{reward.title}` but no alert channel is configured.")
                    continue
                missing = self._missing_bot_perms(guild, channel)
                if missing:
                    notes.append(f"Saved `{reward.title}` but missing bot perms in #{channel.name}: {', '.join(missing)}.")
                    continue
                embed = self._reward_embed(reward, reason=reason)
                try:
                    await channel.send(embed=embed)
                    posted += 1
                except discord.Forbidden:
                    notes.append(f"Saved `{reward.title}` but Discord denied posting in #{channel.name}.")
                except Exception:
                    log.exception("Failed to post Verizon Shine alert guild=%s reward=%s", guild_id, reward.reward_id)
                    notes.append(f"Saved `{reward.title}` but posting failed.")
            else:
                suppressed += 1

        summary = f"Scanned **{len(rewards)}** Verizon Shine item(s). Saved **{saved}**, posted **{posted}**, suppressed duplicates **{suppressed}**."
        if reminders:
            summary += f" Scheduled **{reminders}** reminder(s)."
        if notes:
            summary += "\n\n" + "\n".join(f"• {note}" for note in notes[:6])
        return summary

    async def _resolve_alert_channel(
        self,
        guild: discord.Guild,
        config: VerizonShineConfig,
        *,
        fallback: discord.TextChannel | None = None,
    ) -> discord.TextChannel | None:
        channel_id = config.alert_channel_id
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            return channel
        return fallback

    def _reward_embed(self, reward: VerizonShineReward, *, reason: str | None = None, title_prefix: str = "📡 Verizon Shine Alert") -> discord.Embed:
        color = discord.Color.gold() if reward.priority == "high" else discord.Color.orange()
        embed = discord.Embed(
            title=title_prefix,
            description=f"**{reward.title}**",
            color=color,
        )
        embed.add_field(name="Reward", value="\n".join(build_summary_lines(reward)), inline=False)
        if reward.raw_text:
            embed.add_field(name="Source text", value=reward.raw_text[:900], inline=False)
        if reason:
            embed.add_field(name="Why posted", value=reason, inline=False)
        embed.add_field(
            name="Safety",
            value="Read-only alert. No Verizon password, no auto-claiming, no CAPTCHA bypass.",
            inline=False,
        )
        embed.set_footer(text=f"Source: {reward.source} • First seen: {human_time(reward.first_seen_at)}")
        return embed

    def _missing_bot_perms(self, guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
        me = guild.me
        if me is None:
            return list(REQUIRED_CHANNEL_PERMS.values())
        perms = channel.permissions_for(me)
        missing: list[str] = []
        for attr, label in REQUIRED_CHANNEL_PERMS.items():
            if not getattr(perms, attr, False):
                missing.append(label)
        return missing

    def _missing_permissions_message(self, channel: discord.TextChannel, missing: list[str]) -> str:
        return (
            f"I can see {channel.mention}, but I am missing: **{', '.join(missing)}**.\n"
            "Give SniperPlug those permissions in that channel, then run setup again."
        )

    def _env_enabled(self, name: str, *, default: bool = False) -> bool:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    async def _start_optional_relay(self) -> None:
        if self._relay_started or os.getenv("VERIZON_SHINE_RELAY_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
            return
        secret = os.getenv("VERIZON_SHINE_RELAY_SECRET", "").strip()
        if len(secret) < 16:
            log.warning("VERIZON_SHINE_RELAY_ENABLED is true but VERIZON_SHINE_RELAY_SECRET is missing/too short.")
            return

        try:
            from aiohttp import web
        except Exception:
            log.warning("aiohttp is not installed; Verizon Shine relay endpoint was not started.")
            return

        app = web.Application()

        async def health(_: Any) -> Any:
            return web.json_response({"ok": True, "service": "verizon_shine_relay"})

        async def notification(request: Any) -> Any:
            if not self._relay_authorized(request, secret):
                return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
            try:
                payload = await request.json()
            except Exception:
                return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

            guild_id_raw = payload.get("guild_id")
            try:
                guild_id = int(guild_id_raw)
            except Exception:
                return web.json_response({"ok": False, "error": "guild_id_required"}, status=400)

            title = str(payload.get("title") or payload.get("notification_title") or payload.get("app_title") or "")
            body = str(payload.get("body") or payload.get("text") or payload.get("notification_body") or payload.get("message") or "")
            if not is_relevant_notification(title, body):
                return web.json_response({"ok": True, "ignored": True, "reason": "not_relevant"})

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return web.json_response({"ok": False, "error": "guild_not_found"}, status=404)

            summary = await self._ingest_text(guild, guild_id, f"{title}\n{body}", source="android_relay")
            return web.json_response({"ok": True, "summary": summary})

        app.router.add_get("/health", health)
        app.router.add_post("/verizon/notification", notification)

        runner = web.AppRunner(app)
        await runner.setup()
        host = os.getenv("VERIZON_SHINE_RELAY_HOST", "127.0.0.1").strip() or "127.0.0.1"
        port = int(os.getenv("VERIZON_SHINE_RELAY_PORT", "8082"))
        site = web.TCPSite(runner, host, port)
        await site.start()

        self._relay_runner = runner
        self._relay_site = site
        self._relay_started = True
        log.info("Verizon Shine relay started on %s:%s", host, port)

    def _relay_authorized(self, request: Any, secret: str) -> bool:
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {secret}":
            return True
        for header in ("X-Verizon-Relay-Secret", "X-Webhook-Secret", "X-API-Key"):
            if request.headers.get(header) == secret:
                return True
        return request.query.get("secret") == secret
