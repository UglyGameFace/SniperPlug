from __future__ import annotations

import discord

from sniperplug.services.public_deal_posts import PublicPostResult


def add_public_posting_field(embed: discord.Embed, result: PublicPostResult) -> None:
    """Add a compact manual/public-posting summary to private scan embeds."""

    if not result.any_activity:
        return

    lines = [f"Attempted: **{int(result.attempted or 0)}**", f"Posted: **{int(result.posted or 0)}**"]
    if result.skipped_not_alertable:
        lines.append(f"Skipped: **{result.skipped_not_alertable}** because proof was too weak for public posting")
    if result.cached_active:
        lines.append(f"Already active/cached: **{result.cached_active}**")
    if result.skipped_duplicate:
        lines.append(f"Duplicate skipped: **{result.skipped_duplicate}**")
    if result.skipped_recent_alert_duplicate:
        lines.append(f"Recent alert duplicate skipped: **{result.skipped_recent_alert_duplicate}**")
    if result.skipped_reserved_duplicate:
        lines.append(f"Reserved duplicate skipped: **{result.skipped_reserved_duplicate}**")
    if result.skipped_disabled:
        lines.append(f"Public posting disabled/skipped: **{result.skipped_disabled}**")
    if result.skipped_wrong_retailer:
        lines.append(f"Wrong retailer skipped: **{result.skipped_wrong_retailer}**")
    if result.errors:
        lines.append("Errors: " + "; ".join(str(error) for error in result.errors[:3]))

    embed.add_field(name="📣 Public posting", value="\n".join(lines)[:1024], inline=False)
