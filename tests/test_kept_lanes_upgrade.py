"""Kept-lanes upgrade (2026-11-18): HP / Target / Home Depot / movies.

Felix's directive: HP watcher, Target, Home Depot and movie tickets are
KEPT lanes. Health/status copy must tell the truth (on / off /
waiting-for-key — never the retired "off by design" wording), API-key
lanes must be paste-ready with uniform not-configured messages, and the
movie permission warning must fire once per day instead of every minute.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest import mock

import aiosqlite
import discord

from sniperplug.cogs.movie_tickets import (
    MOVIE_ALERT_CHANNEL_ENV,
    MovieTicketsCog,
    movie_alert_channel_override,
)
from sniperplug.models.local_inventory import LocalInventoryRequest
from sniperplug.providers.home_depot import HomeDepotProvider
from sniperplug.services.hp_watcher_health import HPWatcherHealth
from sniperplug.services.movie_ticket_drops import MovieTicketConfig
from sniperplug.services.target_watcher_health import (
    TargetWatcherHealth,
    load_target_watcher_health,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


# ---------------------------------------------------------------------------
# HP / Target watcher health copy
# ---------------------------------------------------------------------------


def test_hp_not_started_copy_is_truthful_not_off_by_design() -> None:
    line = HPWatcherHealth().summary_line()
    assert "off by design" not in line
    assert "**off**" in line
    # Delivery discipline: HP events ride the verified fanout.
    assert "verified" in line
    # The enable path is named, so the lane reads as ready, not retired.
    assert "SNIPERPLUG_EMBED_HP_WATCHER" in line


def test_target_not_started_without_key_waits_for_key() -> None:
    line = TargetWatcherHealth().summary_line()
    assert "off by design" not in line
    assert "waiting for key" in line
    assert "TARGET_REDSKY_API_KEY" in line


def test_target_not_started_with_key_reports_watcher_off() -> None:
    line = TargetWatcherHealth(redsky_configured=True).summary_line()
    assert "off by design" not in line
    assert "waiting for key" not in line
    assert "**off**" in line


def test_target_health_loader_reads_redsky_env(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        monkeypatch.delenv("TARGET_REDSKY_API_KEY", raising=False)
        health = await load_target_watcher_health(db)
        assert health.redsky_configured is False
        monkeypatch.setenv("TARGET_REDSKY_API_KEY", "test-key")
        health = await load_target_watcher_health(db)
        assert health.redsky_configured is True
        await conn.close()

    asyncio.run(run())


def test_hp_events_route_through_verified_pipeline() -> None:
    from sniperplug.services.hp_deal_cards import build_hp_deal_card
    from sniperplug.services.hp_public_posts import maybe_post_hp_deal_cards
    from sniperplug.services.verified_retailer_event_fanout import (
        retailer_fanout_handler,
    )

    handler = retailer_fanout_handler("hp")
    assert handler is not None
    # Same verified pipeline as every retailer: threshold + dedupe public
    # posts and the shared DM gate — no separate unguarded posting path.
    assert handler.post_cards is maybe_post_hp_deal_cards
    assert handler.build_card is build_hp_deal_card


# ---------------------------------------------------------------------------
# Target setup line (key state is part of the truth)
# ---------------------------------------------------------------------------


def test_target_setup_line_states() -> None:
    from sniperplug.cogs.canonical_workflow import target_setup_line

    ready = target_setup_line(
        target_ready=True, display_name="Bridgeport", redsky_configured=False
    )
    assert "Bridgeport" in ready
    waiting = target_setup_line(
        target_ready=False, display_name=None, redsky_configured=False
    )
    assert "waiting for key" in waiting
    assert "TARGET_REDSKY_API_KEY" in waiting
    paused = target_setup_line(
        target_ready=False, display_name=None, redsky_configured=True
    )
    assert "pick a Target store in /menu" in paused
    assert "waiting for key" not in paused


# ---------------------------------------------------------------------------
# Home Depot: local_check surfaces the SerpApi lane state honestly
# ---------------------------------------------------------------------------


def test_local_check_home_depot_names_serpapi_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    async def run() -> None:
        provider = HomeDepotProvider()
        proof = await provider.check_local_inventory(
            LocalInventoryRequest(retailer="home_depot", sku="334851114")
        )
        assert any("SERPAPI_API_KEY" in warning for warning in proof.warnings)
        # Still honest proof-recording: never claims live stock.
        assert proof.should_public_alert is False

    asyncio.run(run())


def test_local_check_home_depot_notes_serpapi_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    async def run() -> None:
        provider = HomeDepotProvider()
        proof = await provider.check_local_inventory(
            LocalInventoryRequest(retailer="home_depot", sku="334851114")
        )
        assert any(
            "SERPAPI_API_KEY" in warning and "configured" in warning
            for warning in proof.warnings
        )

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Movies: env-configurable destination + once-per-day permission warning
# ---------------------------------------------------------------------------


def test_movie_channel_override_parsing(monkeypatch) -> None:
    monkeypatch.delenv(MOVIE_ALERT_CHANNEL_ENV, raising=False)
    assert movie_alert_channel_override() is None
    monkeypatch.setenv(MOVIE_ALERT_CHANNEL_ENV, "1532848530513854574")
    assert movie_alert_channel_override() == 1532848530513854574
    monkeypatch.setenv(MOVIE_ALERT_CHANNEL_ENV, "not-a-number")
    assert movie_alert_channel_override() is None
    monkeypatch.setenv(MOVIE_ALERT_CHANNEL_ENV, "-5")
    assert movie_alert_channel_override() is None
    monkeypatch.setenv(MOVIE_ALERT_CHANNEL_ENV, "0")
    assert movie_alert_channel_override() is None


def _bare_movie_cog() -> MovieTicketsCog:
    cog = MovieTicketsCog.__new__(MovieTicketsCog)
    cog._destination_warning_at = {}
    return cog


def test_destination_warning_throttled_once_per_day() -> None:
    cog = _bare_movie_cog()
    assert cog._destination_warning_due("permissions", 111, 222) is True
    assert cog._destination_warning_due("permissions", 111, 222) is False
    # A different channel or kind is a different warning.
    assert cog._destination_warning_due("permissions", 111, 333) is True
    assert cog._destination_warning_due("missing", 111, 222) is True
    # After the interval the warning is due again.
    key = ("permissions", 111, 222)
    cog._destination_warning_at[key] -= 24 * 60 * 60 + 1
    assert cog._destination_warning_due("permissions", 111, 222) is True


def test_destination_warning_rearms_after_healthy() -> None:
    cog = _bare_movie_cog()
    assert cog._destination_warning_due("permissions", 111, 222) is True
    assert cog._destination_warning_due("permissions", 111, 222) is False
    cog._note_destination_healthy(111, 222)
    assert cog._destination_warning_due("permissions", 111, 222) is True


def test_effective_channel_id_prefers_env_override(monkeypatch) -> None:
    cog = _bare_movie_cog()
    config = MovieTicketConfig(guild_id=111, alert_channel_id=222, enabled=True)
    monkeypatch.delenv(MOVIE_ALERT_CHANNEL_ENV, raising=False)
    assert cog._effective_channel_id(config) == 222
    monkeypatch.setenv(MOVIE_ALERT_CHANNEL_ENV, "999")
    assert cog._effective_channel_id(config) == 999


def _fake_text_channel(channel_id: int):
    channel = mock.MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    return channel


def test_configured_channel_uses_override_inside_guild(monkeypatch) -> None:
    async def run() -> None:
        cog = _bare_movie_cog()
        override_channel = _fake_text_channel(999)
        config_channel = _fake_text_channel(222)
        channels = {999: override_channel, 222: config_channel}

        async def fetch_channel(channel_id: int):
            raise discord.NotFound(
                mock.MagicMock(status=404, reason="Not Found"), "unknown channel"
            )

        guild = SimpleNamespace(
            get_channel=lambda cid: channels.get(cid),
            fetch_channel=fetch_channel,
        )
        cog.bot = SimpleNamespace(get_guild=lambda gid: guild)
        config = MovieTicketConfig(guild_id=111, alert_channel_id=222, enabled=True)
        monkeypatch.setenv(MOVIE_ALERT_CHANNEL_ENV, "999")
        assert await cog._configured_channel(config) is override_channel

        # An override pointing at another guild's channel falls back to the
        # configured channel instead of breaking this guild's delivery.
        guild.get_channel = lambda cid: {222: config_channel}.get(cid)
        assert await cog._configured_channel(config) is config_channel

    asyncio.run(run())


def test_delivery_permission_warning_fires_once_per_day(caplog) -> None:
    from sniperplug.cogs.multi_source_movie_tickets import (
        MovieTicketsCog as MultiSourceMovieTicketsCog,
    )

    async def run() -> None:
        cog = MultiSourceMovieTicketsCog.__new__(MultiSourceMovieTicketsCog)
        cog._destination_warning_at = {}
        channel = _fake_text_channel(1532848530513854574)
        perms = SimpleNamespace(
            view_channel=False, send_messages=False, embed_links=False
        )
        channel.permissions_for = lambda member: perms
        guild = SimpleNamespace(
            id=4242,
            me=None,
            get_channel=lambda cid: channel,
            fetch_channel=mock.AsyncMock(return_value=channel),
        )
        channel.guild = guild
        cog.bot = SimpleNamespace(user=None, get_guild=lambda gid: guild)
        config = MovieTicketConfig(
            guild_id=4242,
            alert_channel_id=1532848530513854574,
            enabled=True,
        )
        cog.store = SimpleNamespace(
            list_enabled_configs=mock.AsyncMock(return_value=[config]),
            get_config=mock.AsyncMock(return_value=config),
        )
        cog._resolve_poster_urls = mock.AsyncMock(return_value={})
        with caplog.at_level(
            logging.WARNING, logger="sniperplug.movie_tickets.multi_source"
        ):
            await cog._deliver_drops([], target_guild_id=None)
            await cog._deliver_drops([], target_guild_id=None)
        warnings = [
            record
            for record in caplog.records
            if "cannot post" in record.getMessage()
        ]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "1532848530513854574" in message
        assert "View Channel" in message
        assert "Send Messages" in message
        assert "Embed Links" in message

    asyncio.run(run())
