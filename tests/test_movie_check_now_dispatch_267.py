from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sniperplug.cogs.movie_command_guide import GUIDE_LATEST_ID, MovieGuidePanelView


ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "sniperplug/cogs/movie_command_guide.py").read_text(encoding="utf-8")


class _FakeResponse:
    def __init__(self, events: list[str], *, done: bool = False) -> None:
        self.events = events
        self.done = done

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **_: Any) -> None:
        self.events.append("defer")
        self.done = True


class _FakeCog:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def send_latest_from_panel(self, interaction: Any) -> None:
        assert interaction.response.is_done() is True
        self.events.append("delegate")


async def _press_check_now(*, already_done: bool = False) -> list[str]:
    events: list[str] = []
    view = MovieGuidePanelView(_FakeCog(events))  # type: ignore[arg-type]
    button = next(
        item for item in view.children if getattr(item, "custom_id", None) == GUIDE_LATEST_ID
    )
    interaction = SimpleNamespace(response=_FakeResponse(events, done=already_done))
    await button.callback(interaction)  # type: ignore[arg-type]
    return events


def test_check_now_acknowledges_before_delegated_movie_work() -> None:
    assert asyncio.run(_press_check_now()) == ["defer", "delegate"]


def test_check_now_does_not_double_acknowledge_an_existing_response() -> None:
    assert asyncio.run(_press_check_now(already_done=True)) == ["delegate"]


def test_inner_latest_handler_is_safe_after_dispatch_acknowledgement() -> None:
    start = GUIDE.index("async def send_latest_from_panel")
    end = GUIDE.index("async def send_status_from_panel", start)
    handler = GUIDE[start:end]

    assert "if not interaction.response.is_done():" in handler
    assert "interaction.response.defer(ephemeral=True, thinking=True)" in handler
    assert handler.index("interaction.response.is_done") < handler.index("interaction.response.defer")
