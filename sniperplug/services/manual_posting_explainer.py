from __future__ import annotations

from typing import Any

import discord

from sniperplug.services.public_deal_posts import PublicPostResult


def add_public_posting_field(embed: discord.Embed, result: PublicPostResult) -> None:
    """Add a compact manual/public-posting summary to private scan embeds.

    This is an explainer only. It does not post anything and it does not change
    the public deal gate.
    """

    if not result.any_activity:
        return

    attempted = int(result.attempted or 0)
    posted = int(result.posted or 0)
    skipped_not_alertable = int(result.skipped_not_alertable or 0)
    cached_active = int(result.cached_active or 0)

    lines: list[str] = [f"Attempted: **{attempted}**", f"Posted: **{posted}**"]
    if skipped_not_alertable:
        lines.append(f"Skipped: **{skipped_not_alertable}** because proof was too weak for public posting")
    if cached_active:
        lines.append(f"Already active/cached: **{cached_active}**")
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
