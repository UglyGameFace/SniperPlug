"""Discord 2025-26 platform changes (2026-11-22 fix pass).

Pins three behaviour-preserving adaptations:

1. Channel obfuscation (live 2026-11-16): name-based deal-channel
   discovery skips channels Discord hides from the bot (name
   ``___hidden___`` / CHANNEL_OBFUSCATED flag) and, when no sendable
   channel is found, logs a diagnostic that distinguishes "no such
   channel" from "channel exists but is not viewable by the bot".
2. PIN_MESSAGES split (Feb 2026): the one-time consolidation
   announcement checks the optional Pin Messages permission separately
   (NOT via REQUIRED_CHANNEL_PERMS) and logs a clear line before the
   pin attempt; the fail-safe post/pin behaviour is unchanged.
3. Components V2 pilot: HP deal cards carry a LayoutView rendering of
   the same data when SNIPERPLUG_COMPONENTS_V2_CARDS is on, and no
   layout at all when it is off (the default).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.consolidation_announcement import (
    ANNOUNCEMENT_TEXT,
    maybe_post_consolidation_announcement,
    missing_optional_announcement_permissions,
)
from sniperplug.services.hp_deal_cards import build_hp_deal_card
from sniperplug.services.public_alert_config import set_public_alert_config
from sniperplug.services.public_deal_posts import find_named_public_channel
from sniperplug.services.public_posting import CHANNEL_OBFUSCATED_FLAG, channel_is_obfuscated
from sniperplug.services.setup_self_heal import (
    REQUIRED_CHANNEL_PERMS,
    discover_unambiguous_deal_channel,
)
from sniperplug.storage.db import Database


# ---------------------------------------------------------------------------
# 1. Channel obfuscation
# ---------------------------------------------------------------------------


class _FakePerms:
    def __init__(self, **overrides: bool) -> None:
        self.view_channel = True
        self.send_messages = True
        self.embed_links = True
        self.read_message_history = True
        self.pin_messages = True
        for key, value in overrides.items():
            setattr(self, key, value)


class _FakeChannel:
    def __init__(self, channel_id: int, name: str, *, flags: int = 0, perms: _FakePerms | None = None) -> None:
        self.id = channel_id
        self.name = name
        self.flags = flags
        self._perms = perms or _FakePerms()
        self.mention = f"<#{channel_id}>"

    def permissions_for(self, member) -> _FakePerms:  # noqa: ANN001, ANN202
        return self._perms


class _FakeGuild:
    def __init__(self, guild_id: int, channels: list[_FakeChannel]) -> None:
        self.id = guild_id
        self.text_channels = channels
        self.me = SimpleNamespace(id=999)

    def get_channel(self, channel_id: int):  # noqa: ANN202
        return next((c for c in self.text_channels if c.id == channel_id), None)


def test_channel_is_obfuscated_detects_name_and_flag() -> None:
    assert channel_is_obfuscated(SimpleNamespace(name="___hidden___", flags=0)) is True
    assert channel_is_obfuscated(SimpleNamespace(name="deals", flags=CHANNEL_OBFUSCATED_FLAG)) is True
    assert channel_is_obfuscated(SimpleNamespace(name="deals", flags=SimpleNamespace(value=CHANNEL_OBFUSCATED_FLAG))) is True
    assert channel_is_obfuscated(SimpleNamespace(name="deals", flags=0)) is False
    assert channel_is_obfuscated(SimpleNamespace(name="deals")) is False
    assert channel_is_obfuscated(None) is False


def test_discovery_skips_obfuscated_channels(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(discord, "TextChannel", _FakeChannel)
    hidden = _FakeChannel(1, "___hidden___", flags=CHANNEL_OBFUSCATED_FLAG)
    deals = _FakeChannel(2, "deals")
    guild = _FakeGuild(42, [hidden, deals])

    channel, matches = discover_unambiguous_deal_channel(guild)

    assert channel is deals
    assert matches == [deals]


def test_discovery_skips_flagged_channel_even_with_a_deal_name(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(discord, "TextChannel", _FakeChannel)
    # Gateway-obfuscated shape variant: real name leaked but flag set —
    # the flag alone must exclude it.
    flagged = _FakeChannel(1, "deals", flags=CHANNEL_OBFUSCATED_FLAG)
    guild = _FakeGuild(42, [flagged])

    channel, matches = discover_unambiguous_deal_channel(guild)

    assert channel is None
    assert matches == []


def test_discovery_logs_hidden_channel_diagnostic(monkeypatch, caplog) -> None:  # noqa: ANN001
    monkeypatch.setattr(discord, "TextChannel", _FakeChannel)
    hidden = _FakeChannel(1, "___hidden___", flags=CHANNEL_OBFUSCATED_FLAG)
    other = _FakeChannel(2, "general")
    guild = _FakeGuild(42, [hidden, other])

    with caplog.at_level(logging.INFO, logger="sniperplug.setup_self_heal"):
        channel, matches = discover_unambiguous_deal_channel(guild)

    assert channel is None
    assert matches == []
    assert "hidden from the bot" in caplog.text
    assert "obfuscation" in caplog.text


def test_discovery_ambiguity_behaviour_unchanged(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(discord, "TextChannel", _FakeChannel)
    first = _FakeChannel(1, "deals")
    second = _FakeChannel(2, "deals")
    guild = _FakeGuild(42, [first, second])

    channel, matches = discover_unambiguous_deal_channel(guild)

    assert channel is None
    assert matches == [first, second]


@pytest.mark.asyncio
async def test_find_named_public_channel_skips_hidden() -> None:
    hidden = SimpleNamespace(id=1, name="___hidden___", flags=CHANNEL_OBFUSCATED_FLAG)
    deals = SimpleNamespace(id=2, name="deals", flags=0)
    guild = SimpleNamespace(id=42, text_channels=[hidden, deals])

    assert await find_named_public_channel(guild) is deals


@pytest.mark.asyncio
async def test_find_named_public_channel_logs_when_only_hidden(caplog) -> None:  # noqa: ANN001
    hidden = SimpleNamespace(id=1, name="___hidden___", flags=CHANNEL_OBFUSCATED_FLAG)
    guild = SimpleNamespace(id=42, text_channels=[hidden])

    with caplog.at_level(logging.INFO, logger="sniperplug"):
        assert await find_named_public_channel(guild) is None

    assert "hidden from the bot" in caplog.text


# ---------------------------------------------------------------------------
# 2. PIN_MESSAGES future-proofing (announcement path only)
# ---------------------------------------------------------------------------


def test_pin_messages_is_not_a_required_deal_channel_permission() -> None:
    # The split must NOT leak into deal-channel setup: servers that grant
    # deal posting without pinning keep working.
    assert "pin_messages" not in REQUIRED_CHANNEL_PERMS


def test_missing_optional_announcement_permissions() -> None:
    member = SimpleNamespace(id=999)
    lacking = _FakeChannel(1, "deals", perms=_FakePerms(pin_messages=False))
    granted = _FakeChannel(2, "deals", perms=_FakePerms(pin_messages=True))

    assert missing_optional_announcement_permissions(lacking, member) == ["Pin Messages"]
    assert missing_optional_announcement_permissions(granted, member) == []
    assert missing_optional_announcement_permissions(lacking, None) == []
    assert missing_optional_announcement_permissions(SimpleNamespace(), member) == []


class _FakeMessage:
    def __init__(self) -> None:
        self.pinned = False

    async def pin(self) -> None:
        self.pinned = True


class _FakeAnnounceChannel(_FakeChannel):
    def __init__(self, channel_id: int, *, perms: _FakePerms | None = None) -> None:
        super().__init__(channel_id, "deals", perms=perms)
        self.sent: list[str] = []
        self.messages: list[_FakeMessage] = []

    async def send(self, content: str) -> _FakeMessage:
        self.sent.append(content)
        message = _FakeMessage()
        self.messages.append(message)
        return message


class _FakeAnnounceGuild:
    def __init__(self, guild_id: int, channel: _FakeAnnounceChannel) -> None:
        self.id = guild_id
        self.me = SimpleNamespace(id=999)
        self._channel = channel

    def get_channel(self, channel_id: int):  # noqa: ANN202
        return self._channel if channel_id == self._channel.id else None

    async def fetch_channel(self, channel_id: int):  # noqa: ANN202
        return self.get_channel(channel_id)


@pytest.mark.asyncio
async def test_announcement_logs_pin_permission_line_and_still_pins(
    tmp_path: Path, monkeypatch, caplog  # noqa: ANN001
) -> None:
    monkeypatch.setattr(discord, "TextChannel", _FakeAnnounceChannel)
    db = Database(str(tmp_path / "announce-pin.sqlite3"))
    await db.connect()
    await db.init()
    try:
        await set_public_alert_config(
            db, guild_id=111, enabled=True, retailers=("walmart",), channel_id=555
        )
        channel = _FakeAnnounceChannel(555, perms=_FakePerms(pin_messages=False))
        bot = SimpleNamespace(db=db, guilds=[_FakeAnnounceGuild(111, channel)])

        with caplog.at_level(logging.WARNING, logger="sniperplug"):
            assert await maybe_post_consolidation_announcement(bot) == 1

        # Fail-safe behaviour identical: post landed, pin still attempted.
        assert channel.sent == [ANNOUNCEMENT_TEXT]
        assert channel.messages[0].pinned is True
        assert "Pin Messages permission (split from Manage Messages" in caplog.text
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 3. Components V2 pilot (HP deal card)
# ---------------------------------------------------------------------------


def _hp_candidate() -> SourceCandidate:
    return SourceCandidate(
        source_key="hp",
        retailer="HP",
        title="HP Pavilion Laptop 15",
        product_url="https://www.hp.com/us-en/shop/pdp/hp-pavilion-15",
        direct_product_url="https://www.hp.com/us-en/shop/pdp/hp-pavilion-15",
        image_url="https://www.hp.com/example/laptop.png",
        current_price=299.99,
        typical_price=649.99,
        product_id="8XYZ123",
        sku="8XYZ123",
        seller_name="HP.com",
        stock_status="In stock",
        can_add_to_cart=True,
        api_current_price=299.99,
        api_reference_price=649.99,
        api_discount_percent=54.0,
    )


def test_hp_card_has_no_layout_by_default(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("SNIPERPLUG_COMPONENTS_V2_CARDS", raising=False)

    card = build_hp_deal_card(_hp_candidate())

    assert card.layout_view is None
    assert card.embed.title == "HP Pavilion Laptop 15"


def test_hp_card_layout_carries_the_same_data_as_the_embed(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("SNIPERPLUG_COMPONENTS_V2_CARDS", "true")

    card = build_hp_deal_card(_hp_candidate())

    assert isinstance(card.layout_view, discord.ui.LayoutView)
    payload = json.dumps(card.layout_view.to_components())
    embed_payload = json.dumps(card.embed.to_dict())
    for expected in (
        "HP Pavilion Laptop 15",  # title
        "$299.99",  # current price
        "$649.99",  # reference price
        "https://www.hp.com/us-en/shop/pdp/hp-pavilion-15",  # product URL
        "https://www.hp.com/example/laptop.png",  # thumbnail
    ):
        assert expected in payload, expected
        assert expected in embed_payload, expected
    assert card.embed.url == "https://www.hp.com/us-en/shop/pdp/hp-pavilion-15"
