"""Staff screens must not contradict the live posting pipeline.

Two screens Felix screenshotted on 2026-11-20:

* The /staff dashboard Routes page read the legacy per-route channel
  map (guild_alert_channels) and showed "Not set" for every route —
  while the server was in fact posting to its public alert channel.
  Automatic posts never consult that map: the canonical pipeline
  (guild_public_alert_settings + bulk fanout) sends everything to the
  one public channel. The map's only live readers are the staff test
  alert and setup repair (default route as a saved backup).
* The /staff dashboard Overview used the word "Stores" for two
  different facts — the stores this server picked to receive, and the
  provider-registry readiness list, which had no HP line at all — so
  HP showed as selected in one breath and missing in the next.
"""

from __future__ import annotations

from types import SimpleNamespace

import aiosqlite
import pytest

from sniperplug.cogs import canonical_settings
from sniperplug.cogs.sniperplug import SniperPlugCog
from sniperplug.services.hp_watcher_health import HPWatcherHealth
from sniperplug.services.public_alert_config import set_public_alert_config


GUILD_ID = 424242
PUBLIC_CHANNEL_ID = 1518804442718146560


class _HybridDB:
    """require_conn() for the real config service + a canned stats()."""

    def __init__(self, conn, alert_routes):
        self._conn = conn
        self._alert_routes = alert_routes

    def require_conn(self):
        return self._conn

    async def stats(self, guild_id):
        return {"alert_routes": dict(self._alert_routes)}


class _CapturingResponse:
    def __init__(self):
        self.kwargs = None

    async def send_message(self, **kwargs):
        self.kwargs = kwargs


async def _routes_embed(alert_routes):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = _HybridDB(conn, alert_routes)
    try:
        await set_public_alert_config(
            db,
            guild_id=GUILD_ID,
            enabled=True,
            retailers=("walmart", "hp"),
            channel_id=PUBLIC_CHANNEL_ID,
        )
        cog = SniperPlugCog(SimpleNamespace(db=db))
        interaction = SimpleNamespace(
            guild_id=GUILD_ID, response=_CapturingResponse()
        )
        await cog.routes(interaction)
    finally:
        await conn.close()
    assert interaction.response.kwargs is not None
    return interaction.response.kwargs["embed"]


@pytest.mark.asyncio
async def test_routes_page_shows_public_channel_when_no_route_overrides() -> None:
    embed = await _routes_embed({})
    # The effective destination is stated up front, in plain words.
    assert embed.description is not None
    assert f"<#{PUBLIC_CHANNEL_ID}>" in embed.description
    assert "Automatic deal posts" in embed.description
    assert "legacy" in embed.description
    # No route claims "Not set" while the server is posting; an unset
    # override is labeled as exactly that.
    assert embed.fields
    for field in embed.fields:
        assert not field.value.startswith("Not set")
        assert "No separate channel set" in field.value


@pytest.mark.asyncio
async def test_routes_page_still_shows_a_real_route_override() -> None:
    embed = await _routes_embed({"price_glitch": 777})
    values = {field.name: field.value for field in embed.fields}
    assert "<#777>" in values["Price Glitches"]
    assert f"<#{PUBLIC_CHANNEL_ID}>" in (embed.description or "")


# ---------------------------------------------------------------------------
# Overview dashboard
# ---------------------------------------------------------------------------


async def _overview_embed(monkeypatch):
    async def fake_config(db, guild_id):
        return {
            "enabled": True,
            "retailers": ("walmart", "hp"),
            "channel_id": PUBLIC_CHANNEL_ID,
        }

    async def fake_threshold(db, guild_id):
        return 40

    async def fake_healthchecks():
        return (
            SimpleNamespace(
                status=SimpleNamespace(value="ready"),
                ok=True,
                provider_key="walmart",
                message="Walmart Affiliate API credentials are configured.",
            ),
        )

    async def fake_active_counts(db, guild_id):
        return {}

    async def fake_membership(db, bot, guild_id):
        return True, "This server is present in the live scheduled autoscan set."

    async def fake_hp_health(db):
        return HPWatcherHealth(
            status="healthy",
            stale=False,
            products=4563,
            identified_products=4278,
            active_markdowns=3,
            due_offers=45,
            pending_events=0,
        )

    monkeypatch.setattr(canonical_settings, "get_public_alert_config", fake_config)
    monkeypatch.setattr(canonical_settings, "get_starting_deal_percent", fake_threshold)
    monkeypatch.setattr(
        canonical_settings,
        "provider_registry",
        SimpleNamespace(healthchecks=fake_healthchecks),
    )
    monkeypatch.setattr(canonical_settings, "active_deal_counts", fake_active_counts)
    monkeypatch.setattr(
        canonical_settings, "scheduler_membership_for_guild", fake_membership
    )
    monkeypatch.setattr(
        canonical_settings,
        "load_global_catalog_state",
        lambda db: _async_value(
            SimpleNamespace(
                summary_line=lambda total_routes: "global catalog state: ok"
            )
        ),
    )
    monkeypatch.setattr(
        canonical_settings,
        "load_walmart_exact_queue_health",
        lambda db: _async_value(
            SimpleNamespace(summary_line=lambda: "queue health: ok")
        ),
    )
    monkeypatch.setattr(canonical_settings, "load_hp_watcher_health", fake_hp_health)

    bot = SimpleNamespace(db=SimpleNamespace())
    return await canonical_settings.build_overview_dashboard(bot, GUILD_ID)


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_overview_distinguishes_received_stores_from_connections(monkeypatch) -> None:
    embed = await _overview_embed(monkeypatch)
    fields = {field.name: field.value for field in embed.fields}

    # One word is no longer used for two different facts.
    assert "Stores" not in fields
    this_server = fields["This server"]
    assert "Stores this server receives: Walmart, HP" in this_server

    connections = fields["Store connections"]
    assert "walmart" in connections
    # HP readiness is present, from the shared watcher health source,
    # instead of HP vanishing from the readiness list.
    assert "HP watcher **healthy**" in connections
    assert "4278/4563 identified" in connections
