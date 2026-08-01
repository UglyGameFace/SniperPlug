from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sniperplug.cogs.movie_command_guide import (
    GUIDE_LATEST_ID,
    GUIDE_SELECT_ID,
    GUIDE_STATUS_ID,
    MovieCommandGuideCog,
    MovieGuidePanelView,
    MovieGuideSectionSelect,
    build_movie_guide_home_embed,
    build_movie_guide_section_embed,
)
from sniperplug.cogs.registered_multi_source_movies import MovieTicketsCog


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
COG = (ROOT / "sniperplug/cogs/movie_command_guide.py").read_text(encoding="utf-8")


class _FakeBot:
    def __init__(self) -> None:
        self.db = object()
        self.cogs: dict[str, Any] = {}
        self.user = None

    def get_cog(self, name: str) -> Any | None:
        return self.cogs.get(name)


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred = False

    async def defer(self, **_: Any) -> None:
        self.deferred = True

    def is_done(self) -> bool:
        return self.deferred


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


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
    for source_name in ("Atom", "Fandango", "Gofobo"):
        assert source_name in rendered


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
    assert 'label="Atom"' in COG
    assert 'label="Fandango"' in COG
    assert 'label="Gofobo"' in COG
    assert view is not None


def test_movie_group_cog_is_resolved_by_registered_name_and_type() -> None:
    bot = _FakeBot()
    movie_cog = MovieTicketsCog(bot)  # type: ignore[arg-type]
    bot.cogs[MovieTicketsCog.__cog_name__] = movie_cog
    guide_cog = MovieCommandGuideCog(bot)  # type: ignore[arg-type]

    assert MovieTicketsCog.__cog_name__ == "movies"
    assert guide_cog._movie_cog() is movie_cog
    assert "get_cog(MovieTicketsCog.__cog_name__)" in COG
    assert 'get_cog("MovieTicketsCog")' not in COG


def test_dropdown_acknowledges_before_sending_private_section() -> None:
    bot = _FakeBot()
    guide_cog = MovieCommandGuideCog(bot)  # type: ignore[arg-type]
    select = MovieGuideSectionSelect(guide_cog)
    response = _FakeResponse()
    followup = _FakeFollowup()
    interaction = SimpleNamespace(
        response=response,
        followup=followup,
        data={"values": ["start"]},
        user=SimpleNamespace(id=123),
        guild_id=456,
    )

    asyncio.run(select.callback(interaction))  # type: ignore[arg-type]

    assert response.deferred is True
    assert len(followup.messages) == 1
    assert followup.messages[0]["ephemeral"] is True
    assert followup.messages[0]["embed"].title == "🎬 Start Here"


def test_component_failures_have_a_visible_fallback() -> None:
    assert "async def on_error(" in COG
    assert "await _send_component_error(interaction, error)" in COG
    assert "That movie panel action hit an error" in COG


def test_public_panel_interactions_reply_privately() -> None:
    assert COG.count("ephemeral=True") >= 10
    assert "allowed_mentions=discord.AllowedMentions.none()" in COG
    assert "missing_channel_permissions" in COG
