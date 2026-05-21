from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.monitor_control import MonitorMode, build_default_monitor_control_plane


class MonitorCog(commands.GroupCog, name="monitor"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="plan", description="Show SniperPlug live monitor targets without scanning retailers.")
    @app_commands.describe(source_key="Optional source filter, like amazon, best_buy, walmart, msi_store, nike.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def plan(self, interaction: discord.Interaction, source_key: str | None = None) -> None:
        control_plane = build_default_monitor_control_plane(limit_targets=24)
        targets = control_plane.targets
        if source_key:
            targets = control_plane.by_source(source_key)

        embed = discord.Embed(
            title="SniperPlug Monitor Plan",
            description=(
                "Control-plane preview only. This does not scan retailers, call APIs, "
                "or post public alerts. Generated monitors default to Staff Review."
            ),
            color=discord.Color.orange(),
        )

        if not targets:
            embed.add_field(
                name="No monitor targets",
                value="No targets matched that filter.",
                inline=False,
            )
        else:
            for target in targets[:12]:
                terms = ", ".join(target.watch_terms[:4]) or "None"
                embed.add_field(
                    name=f"{mode_label(target.mode)} • {target.source_name} + {target.category_label}",
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

    @plan.error
    async def monitor_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You need **Manage Server** permission to use monitor commands."
        else:
            message = f"SniperPlug monitor command hit an error: `{error}`"

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def mode_label(mode: MonitorMode) -> str:
    labels = {
        MonitorMode.PREVIEW_ONLY: "🔎 Preview",
        MonitorMode.STAFF_REVIEW: "🛠️ Staff Review",
        MonitorMode.PUBLIC_ALLOWED: "📣 Public Allowed",
    }
    return labels.get(mode, mode.value)
