import discord

from sniperplug.cogs import deal_scanner
from sniperplug.services.resale_hunt import (
    RESALE_HUNT_KEY,
    ResaleHuntButton,
    batch_cards_for_embed_limit,
    embed_text_size,
    install_resale_hunt_preset,
    short_error,
)


def test_resale_hunt_installs_dedicated_button_class():
    install_resale_hunt_preset()

    view = deal_scanner.HuntPresetMenuView()
    resale_buttons = [child for child in view.children if getattr(child, "preset", None) and child.preset.key == RESALE_HUNT_KEY]

    assert resale_buttons
    assert isinstance(resale_buttons[0], ResaleHuntButton)


def test_resale_hunt_button_label_still_matches_preset():
    install_resale_hunt_preset()

    preset = deal_scanner.HUNT_PRESETS[RESALE_HUNT_KEY]
    button = ResaleHuntButton(preset, row=0)

    assert button.label == "Resale Hunt"
    assert str(button.emoji) == "♻️"


def test_batch_cards_for_embed_limit_splits_rich_cards():
    cards = []
    for idx in range(3):
        embed = discord.Embed(title=f"Card {idx}", description="x" * 3000)
        cards.append(deal_scanner.DealCard(embed=embed, url=f"https://example.com/{idx}", label=f"Card {idx}"))

    batches = batch_cards_for_embed_limit(cards, limit=5200)

    assert len(batches) == 3
    assert all(len(batch) == 1 for batch in batches)


def test_embed_text_size_counts_fields():
    embed = discord.Embed(title="A", description="B")
    embed.add_field(name="C", value="D", inline=False)

    assert embed_text_size(embed) == 4


def test_short_error_truncates_long_discord_errors():
    error = RuntimeError("x" * 2000)

    rendered = short_error(error, limit=100)

    assert len(rendered) <= 100
    assert rendered.endswith("…")
