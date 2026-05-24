from __future__ import annotations

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.services.public_result_explainer import explain_public_post_result


MAX_PUBLIC_POSTING_FIELD = 1000


def install_manual_posting_explainer_patch() -> None:
    """Replace terse manual scan posting summaries with useful owner-facing reasons.

    The scan commands already call `add_public_posting_field()` in deal_scanner.
    This patch keeps the command surface stable while making `/deals`, `/hunt`,
    rerun buttons, and 80% hunt explain why cards did or did not post publicly.
    """
    if getattr(deal_scanner, "_sniperplug_manual_posting_explainer_installed", False):
        return
    deal_scanner.add_public_posting_field = add_public_posting_field
    deal_scanner._sniperplug_manual_posting_explainer_installed = True


def add_public_posting_field(embed: discord.Embed, public_result) -> None:
    if not getattr(public_result, "any_activity", False):
        return
    embed.add_field(
        name="📣 Public posting",
        value=truncate_public_field(explain_public_post_result(public_result)),
        inline=False,
    )


def truncate_public_field(value: str) -> str:
    text = str(value).strip() or "Public posting ran, but no detailed result was returned."
    return text if len(text) <= MAX_PUBLIC_POSTING_FIELD else text[: MAX_PUBLIC_POSTING_FIELD - 3].rstrip() + "..."
