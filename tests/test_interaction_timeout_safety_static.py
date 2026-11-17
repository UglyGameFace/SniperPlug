from pathlib import Path

DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")


def test_safe_defer_exists_and_catches_expired_interactions():
    assert "async def safe_defer(" in DEALS
    assert "discord.NotFound" in DEALS
    assert "discord.HTTPException" in DEALS
    assert "discord.InteractionResponded" in DEALS
    assert "return False" in DEALS


def test_walmart_cash_uses_safe_defer_before_scan_work():
    expected = (
        "if not await safe_defer(interaction, ephemeral=True, thinking=True):\n"
        "            return\n"
        "        await self._send_walmart_cash_search"
    )
    assert expected in DEALS


def test_walmart_cash_button_removed_so_no_button_callback_to_safe_defer():
    # /walmart cash was retired (no Cash offer feed exists); the Hunt
    # "Walmart Cash Offers" button is gone, so there is no button callback
    # that needs safe_defer. The cash search path itself still safe-defers
    # (see test_walmart_cash_uses_safe_defer_before_scan_work).
    assert 'custom_id="hunt:walmart_cash_offers"' not in DEALS
    assert "WalmartCashOffersButton" not in DEALS


def test_command_error_sender_does_not_raise_second_unknown_interaction():
    assert "async def send_command_error(" in DEALS
    assert "await safe_send_interaction(interaction, message, ephemeral=True)" in DEALS
    assert "await interaction.response.send_message(message, ephemeral=True)" not in DEALS
