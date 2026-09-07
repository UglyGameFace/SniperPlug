from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sniperplug.cogs.movie_command_guide import (
    GUIDE_ENABLE_HERE_ID,
    GUIDE_ENABLE_HERE_PANEL_ID,
    GUIDE_LATEST_ID,
    GUIDE_SELECT_ID,
    GUIDE_STATUS_ID,
    MovieCommandGuideCog,
    MovieGuidePanelView,
    MovieGuideSectionSelect,
    MovieStatusActionView,
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

    async def send_message(self, *_: Any, **__: Any) -> None:
        self.deferred = True

    def is_done(self) -> bool:
        return self.deferred


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, *args: Any, **kwargs: Any) -> None:
        if args:
            kwargs["content"] = args[0]
        self.messages.append(kwargs)


class _FakePanelMessage:
    def __init__(self) -> None:
        self.id = 7312026
        self.author = SimpleNamespace(id=999)
        self.edits: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> None:
        self.edits.append(kwargs)


def test_runtime_registers_one_movie_command_guide_cog() -> None:
    assert "from sniperplug.cogs.movie_command_guide import MovieCommandGuideCog" in BOT
    assert BOT.count("await self.add_cog(MovieCommandGuideCog(self))") == 1


def test_guide_commands_are_available_and_panel_posting_is_manager_only() -> None:
    assert '@app_commands.command(\n        name="movies-help"' in COG
    assert '@app_commands.command(\n        name="movies-panel"' in COG
    panel_start = COG.index("async def movies_panel(")
    assert "@app_commands.checks.has_permissions(manage_guild=True)" in COG[:panel_start]
    assert "target_channel: discord.TextChannel | None = None" in COG


def test_home_panel_is_action_first_not_a_command_manual() -> None:
    embed = build_movie_guide_home_embed()
    rendered = "\n".join(
        [embed.title or "", embed.description or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )
    for action in ("Check Now", "Alerts Here", "Status"):
        assert action in rendered
    for source_name in (
        "Atom",
        "Fandango",
        "Gofobo",
        "SeeItEarly",
        "AdvanceScreenings",
        "1iota",
    ):
        assert source_name in rendered
    assert "/movies setup alert_channel:#channel" not in rendered
    assert "/movies test-alert" not in rendered


def test_every_guide_section_has_clear_instructional_content() -> None:
    for section in ("start", "users", "admins", "safety"):
        embed = build_movie_guide_section_embed(section)
        assert embed.title
        assert embed.fields
        assert all(field.name and field.value for field in embed.fields)


def test_panel_view_is_persistent_and_uses_explicit_action_rows() -> None:
    bot = _FakeBot()
    guide_cog = MovieCommandGuideCog(bot)  # type: ignore[arg-type]
    view = MovieGuidePanelView(guide_cog)
    assert GUIDE_SELECT_ID == "movies:guide:section"
    assert GUIDE_LATEST_ID == "movies:guide:latest"
    assert GUIDE_STATUS_ID == "movies:guide:status"
    assert GUIDE_ENABLE_HERE_ID == "movies:guide:enable_here"
    assert GUIDE_ENABLE_HERE_PANEL_ID == "movies:guide:enable_here_panel"
    assert "timeout=None" in COG
    assert "self.bot.add_view(MovieGuidePanelView(self))" in COG

    items = {getattr(item, "label", ""): item for item in view.children}
    for label in ("Check Now", "Status", "Alerts Here"):
        assert label in items
        assert items[label].row == 1
    for label in ("Atom", "Fandango", "Gofobo"):
        assert label in items
        assert items[label].row == 2
    for label in ("SeeItEarly", "AdvanceScreenings", "1iota"):
        assert label in items
        assert items[label].row == 3


def test_status_action_view_uses_shared_current_channel_setup() -> None:
    view = MovieStatusActionView(None)  # type: ignore[arg-type]
    button = next(item for item in view.children if getattr(item, "custom_id", None) == GUIDE_ENABLE_HERE_ID)
    assert button.label == "Use This Channel for Alerts"
    assert "await self.cog.enable_alerts_here(interaction)" in COG
    assert "guild_permissions.manage_guild" in COG
    assert "MovieTicketConfig(" in COG
    assert "alert_channel_id=int(channel.id)" in COG
    assert "movie_cog.store.save_config" in COG


def test_old_permanent_panel_self_refreshes_exact_interaction_message() -> None:
    bot = _FakeBot()
    guide_cog = MovieCommandGuideCog(bot)  # type: ignore[arg-type]
    message = _FakePanelMessage()
    interaction = SimpleNamespace(guild_id=456, message=message)

    refreshed = asyncio.run(guide_cog.refresh_public_panel_from_interaction(interaction))  # type: ignore[arg-type]

    assert refreshed is True
    assert len(message.edits) == 1
    assert message.edits[0]["embed"].title == "🎬 Free Movie Tickets & Screenings"
    refreshed_view = message.edits[0]["view"]
    assert isinstance(refreshed_view, MovieGuidePanelView)
    labels = {getattr(item, "label", "") for item in refreshed_view.children}
    assert {"Check Now", "Status", "Alerts Here"}.issubset(labels)


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
        message=None,
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
