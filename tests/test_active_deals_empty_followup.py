from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from sniperplug.cogs.active_deals import ActiveDealPage, send_active_deals_followup


class RecordingFollowup:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    async def send(self, **kwargs) -> None:
        self.payload = dict(kwargs)


def test_empty_active_deals_followup_omits_none_view() -> None:
    followup = RecordingFollowup()
    interaction = SimpleNamespace(followup=followup)
    page = ActiveDealPage(rows=[], total=0, page=1, page_size=10)

    asyncio.run(send_active_deals_followup(interaction, 123, page))

    assert followup.payload is not None
    assert "view" not in followup.payload
    assert followup.payload["ephemeral"] is True
    assert isinstance(followup.payload["embed"], discord.Embed)


def test_nonempty_active_deals_followup_keeps_interactive_view() -> None:
    followup = RecordingFollowup()
    interaction = SimpleNamespace(followup=followup)
    page = ActiveDealPage(
        rows=[
            {
                "active_key": "target:item:1",
                "retailer": "target",
                "title": "Example deal",
                "url": "https://example.com/deal",
                "current_price": 10.0,
                "discount": 50.0,
                "score": 95,
                "source_label": "test",
                "last_seen_at": "2026-08-05T00:00:00+00:00",
            }
        ],
        total=1,
        page=1,
        page_size=10,
    )

    asyncio.run(send_active_deals_followup(interaction, 123, page))

    assert followup.payload is not None
    assert isinstance(followup.payload["view"], discord.ui.View)
    assert followup.payload["ephemeral"] is True
