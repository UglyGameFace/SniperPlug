from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COG = (ROOT / "sniperplug/cogs/movie_tickets.py").read_text(encoding="utf-8")


def test_movies_has_public_guide_panel_and_private_help_commands() -> None:
    assert '@app_commands.command(name="help"' in COG
    assert '@app_commands.command(name="panel"' in COG
    assert "class MovieGuidePanelView(discord.ui.View)" in COG
    assert "class MovieGuideSectionSelect(discord.ui.Select)" in COG


def test_guide_panel_explains_every_movies_command() -> None:
    for command in (
        "/movies help",
        "/movies panel",
        "/movies latest",
        "/movies setup",
        "/movies status",
        "/movies scan",
        "/movies test-alert",
        "/movies sources",
        "/movies disable",
    ):
        assert command in COG


def test_guide_panel_is_persistent_and_has_live_latest_button() -> None:
    assert 'custom_id="movies:guide:section"' in COG
    assert 'custom_id="movies:guide:latest"' in COG
    assert 'timeout=None' in COG
    assert 'self.bot.add_view(MovieGuidePanelView(self))' in COG
    assert "await self._send_latest_response(interaction)" in COG


def test_panel_posting_requires_manage_guild_and_channel_permissions() -> None:
    assert "async def panel(" in COG
    assert "@app_commands.checks.has_permissions(manage_guild=True)" in COG
    assert "target_channel: discord.TextChannel | None = None" in COG
    assert "_missing_bot_permissions" in COG
