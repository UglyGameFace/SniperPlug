from __future__ import annotations

from pathlib import Path

from sniperplug.cogs.movie_command_guide import (
    GUIDE_LATEST_ID,
    GUIDE_SELECT_ID,
    GUIDE_STATUS_ID,
    MovieGuidePanelView,
    build_movie_guide_home_embed,
    build_movie_guide_section_embed,
)


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
COG = (ROOT / "sniperplug/cogs/movie_command_guide.py").read_text(encoding="utf-8")


def test_runtime_registers_one_movie_command_guide_cog() -> None:
    assert "from sniperplug.cogs.movie_command_guide import MovieCommandGuideCog" in BOT
    assert BOT.count("await self.add_cog(MovieCommandGuideCog(self))") == 1


def test_guide_commands_are_available_and_panel_posting_is_manager_only() -> None:
    assert '@app_commands.command(\n        name="movies-help"' in COG
    assert '@app_commands.command(\n        name="movies-panel"' in COG
    panel_start = COG.index("async def movies_panel(")
    assert "@app_commands.checks.has_permissions(manage_guild=True)" in COG[:panel_start]
    assert "target_channel: discord.TextChannel | None = None" in COG


def test_home_panel_explains_every_current_movie_command() -> None:
    embed = build_movie_guide_home_embed()
    rendered = "\n".join(
        [embed.title or "", embed.description or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )
    for command in (
        "/movies latest",
        "/movies status",
        "/movies sources",
        "/movies setup alert_channel:#channel",
        "/movies scan",
        "/movies test-alert",
        "/movies disable",
        "/movies-help",
        "/movies-panel target_channel:#channel",
    ):
        assert command in rendered


def test_every_guide_section_has_clear_instructional_content() -> None:
    for section in ("start", "users", "admins", "safety"):
        embed = build_movie_guide_section_embed(section)
        assert embed.title
        assert embed.fields
        assert all(field.name and field.value for field in embed.fields)


def test_panel_view_is_persistent_and_has_live_controls() -> None:
    view = MovieGuidePanelView.__new__(MovieGuidePanelView)
    assert GUIDE_SELECT_ID == "movies:guide:section"
    assert GUIDE_LATEST_ID == "movies:guide:latest"
    assert GUIDE_STATUS_ID == "movies:guide:status"
    assert "timeout=None" in COG
    assert "self.bot.add_view(MovieGuidePanelView(self))" in COG
    assert "await self.cog.send_latest_from_panel(interaction)" in COG
    assert "await self.cog.send_status_from_panel(interaction)" in COG
    assert view is not None


def test_public_panel_interactions_reply_privately() -> None:
    assert COG.count("ephemeral=True") >= 10
    assert "allowed_mentions=discord.AllowedMentions.none()" in COG
    assert "missing_channel_permissions" in COG
