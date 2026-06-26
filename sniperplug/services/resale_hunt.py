from __future__ import annotations

import asyncio

import discord

from sniperplug.cogs import deal_scanner


RESALE_HUNT_KEY = "resale"
RESALE_HUNT_TIMEOUT_SECONDS = 90
SAFE_EMBED_MESSAGE_LIMIT = 5200
MAX_EMBEDS_PER_MESSAGE = 10

RESALE_HUNT_QUERIES = (
    "restored laptop",
    "restored iphone",
    "restored tv",
    "refurbished nintendo switch",
    "open box power tool",
    "like new laptop",
    "like new iphone",
    "like new electronics",
)


def install_resale_hunt_preset() -> None:
    """Install the resale hunt button into the existing /hunt menu.

    The base hunt menu is intentionally simple and button-driven. This installer
    adds a dedicated resale/open-box/refurbished hunt without changing the public
    slash command shape.
    """
    deal_scanner.HUNT_PRESETS[RESALE_HUNT_KEY] = deal_scanner.HuntPreset(
        RESALE_HUNT_KEY,
        "Resale Hunt",
        "♻️",
        "Open-box, restored, refurbished, and like-new leads across flip-friendly categories.",
        RESALE_HUNT_QUERIES,
        25,
    )

    if getattr(deal_scanner.HuntPresetMenuView, "_sniperplug_resale_installed", False):
        return

    def patched_init(self) -> None:
        deal_scanner.discord.ui.View.__init__(self, timeout=300)
        layout = (
            ("glitch", 0),
            (RESALE_HUNT_KEY, 0),
            ("tech", 0),
            ("essentials", 1),
            ("home", 1),
            ("toys", 1),
            ("auto_tools", 2),
        )
        for key, row in layout:
            preset = deal_scanner.HUNT_PRESETS[key]
            button_cls = ResaleHuntButton if key == RESALE_HUNT_KEY else deal_scanner.HuntPresetButton
            self.add_item(button_cls(preset, row=row))

    deal_scanner.HuntPresetMenuView.__init__ = patched_init
    deal_scanner.HuntPresetMenuView._sniperplug_resale_installed = True


class ResaleHuntButton(deal_scanner.HuntPresetButton):
    """Resale hunt uses the same pipeline, but fails visibly instead of freezing.

    Resale queries can be slower because Walmart condition/refurbished searches
    are broad and third-party-heavy. A timeout + error path keeps Discord from
    looking stuck forever when the provider/API stalls or one query explodes.
    """

    async def callback(self, interaction: discord.Interaction) -> None:
        lock_key = deal_scanner.ScanLockKey(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            action="hunt_preset",
            preset=self.preset.key,
            min_discount=self.preset.min_discount,
        )
        if not await deal_scanner.acquire_scan_lock(
            interaction,
            lock_key,
            self.view,
            "⏳ Running **Resale Hunt** for open-box/restored flip leads. Buttons are locked so this cannot double-post...",
        ):
            return
        try:
            health_error = await deal_scanner.provider_health_error_message()
            if health_error:
                await interaction.followup.send(health_error, ephemeral=True)
                return

            cards, pages_checked, products_checked, warnings, shown_discount = await asyncio.wait_for(
                deal_scanner.run_preset_hunt(self.preset, str(interaction.user.id)),
                timeout=RESALE_HUNT_TIMEOUT_SECONDS,
            )
            summary = deal_scanner.build_preset_hunt_summary(
                self.preset,
                pages_checked,
                products_checked,
                len(cards),
                tuple(warnings),
                shown_discount,
            )
            summary.add_field(
                name="Flip filter",
                value="Targets condition-specific leads like **Restored**, **Refurbished**, **Open Box**, and **Like New**. Always verify sell-through and condition before buying inventory.",
                inline=False,
            )
            if not cards:
                summary.add_field(
                    name="Nothing useful found yet",
                    value="No flip-worthy resale candidates came back from the preset searches. Try again later or use `/deals search:restored laptop` for a tighter manual search.",
                    inline=False,
                )
                await interaction.followup.send(embed=summary, view=deal_scanner.HuntPresetMenuView(), ephemeral=True)
                return

            shown_cards = cards[:5]
            public_result = await deal_scanner.maybe_post_public_deal_cards(
                bot=interaction.client,
                guild_id=interaction.guild_id,
                cards=shown_cards,
                source_label="hunt:resale",
                fallback_retailer="walmart",
            )
            deal_scanner.add_public_posting_field(summary, public_result)
            await send_resale_results(interaction, summary=summary, cards=shown_cards)
        except asyncio.TimeoutError:
            embed = discord.Embed(
                title="♻️ Resale Hunt timed out safely",
                description="The Walmart resale/refurbished searches took too long, so SniperPlug stopped the run instead of leaving the menu frozen.",
                color=discord.Color.dark_gold(),
            )
            embed.add_field(
                name="Try this next",
                value="Use a tighter manual search like `/deals search:restored laptop`, `/deals search:restored tv`, or `/deals search:refurbished nintendo switch`.",
                inline=False,
            )
            await interaction.followup.send(embed=embed, view=deal_scanner.HuntPresetMenuView(), ephemeral=True)
        except Exception as exc:
            embed = discord.Embed(
                title="♻️ Resale Hunt hit an error",
                description=f"SniperPlug stopped the run instead of freezing. Error: `{short_error(exc)}`",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, view=deal_scanner.HuntPresetMenuView(), ephemeral=True)
        finally:
            await deal_scanner.scan_operation_locks.release(lock_key)


async def send_resale_results(
    interaction: discord.Interaction,
    *,
    summary: discord.Embed,
    cards: list[deal_scanner.DealCard],
) -> None:
    """Send resale results without exceeding Discord's combined embed limit.

    Discord allows each embed to be large, but the combined embed text in one
    message must stay below 6000 characters. Rich Walmart proof cards can blow
    past that when summary + multiple cards are sent together, so send the
    summary first and chunk product cards into safe batches.
    """
    await interaction.followup.send(embed=summary, ephemeral=True)
    for batch in batch_cards_for_embed_limit(cards, limit=SAFE_EMBED_MESSAGE_LIMIT):
        await interaction.followup.send(
            embeds=[card.embed for card in batch],
            view=deal_scanner.PresetResultView(batch),
            ephemeral=True,
        )


def batch_cards_for_embed_limit(
    cards: list[deal_scanner.DealCard],
    *,
    limit: int = SAFE_EMBED_MESSAGE_LIMIT,
) -> list[list[deal_scanner.DealCard]]:
    batches: list[list[deal_scanner.DealCard]] = []
    current: list[deal_scanner.DealCard] = []
    current_size = 0

    for card in cards:
        size = embed_text_size(card.embed)
        if current and (current_size + size > limit or len(current) >= MAX_EMBEDS_PER_MESSAGE):
            batches.append(current)
            current = []
            current_size = 0
        current.append(card)
        current_size += size

    if current:
        batches.append(current)
    return batches


def embed_text_size(embed: discord.Embed) -> int:
    data = embed.to_dict()
    total = len(str(data.get("title") or ""))
    total += len(str(data.get("description") or ""))

    footer = data.get("footer") or {}
    total += len(str(footer.get("text") or ""))

    author = data.get("author") or {}
    total += len(str(author.get("name") or ""))

    for field in data.get("fields") or []:
        total += len(str(field.get("name") or ""))
        total += len(str(field.get("value") or ""))
    return total


def short_error(exc: Exception, *, limit: int = 900) -> str:
    text = f"{type(exc).__name__}: {exc}"
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
