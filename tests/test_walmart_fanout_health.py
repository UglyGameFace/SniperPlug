"""Fanout health persistence, the silent-outage alarm, and the health
screen's turn-away field.

The incident shape under test is 2026-11-18: deals flowed into the
public fanout for ~40 hours, every one was turned away by the live
pre-send recheck, zero posts landed, and nobody knew. The alarm must
fire on exactly that shape — and stay quiet for genuine quiet, healthy
posting, and repeat evaluations inside one episode.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from sniperplug.services.public_alert_config import ensure_public_alert_table
from sniperplug.services.walmart_fanout_health import (
    FanoutCycleTotals,
    build_outage_alarm_message,
    evaluate_outage,
    evaluate_outage_snapshot,
    load_fanout_health,
    maybe_send_outage_alarm,
    record_fanout_cycle,
    resolve_owner_user_id,
)


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class FakeUser:
    def __init__(self, *, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, content):
        if self.fail:
            raise RuntimeError("dm boom")
        self.sent.append(content)


class FakeBot:
    def __init__(self, user: FakeUser, *, owner_id: int = 424242):
        self.user = user
        self.owner_id = owner_id

    def get_user(self, user_id: int):
        return self.user if int(user_id) == self.owner_id else None

    async def fetch_user(self, user_id: int):
        return self.get_user(user_id)


async def _db() -> FakeDatabase:
    conn = await aiosqlite.connect(":memory:")
    return FakeDatabase(conn)


async def _enroll_one_guild(db: FakeDatabase) -> None:
    await ensure_public_alert_table(db)
    conn = db.require_conn()
    await conn.execute(
        "INSERT INTO guild_public_alert_settings "
        "(guild_id, enabled, retailers_json, channel_id, created_at, updated_at) "
        "VALUES (?, 1, '[\"walmart\"]', NULL, ?, ?)",
        (111000, NOW.isoformat(), NOW.isoformat()),
    )
    await conn.commit()


async def _seed_incident(db: FakeDatabase) -> None:
    """The 2026-11-18 shape: a last good post 4h ago, then a stream of
    cycles that claim deals and turn every one away at the recheck."""

    await record_fanout_cycle(
        db,
        FanoutCycleTotals(events_claimed=3, events_processed=3, public_posts=1),
        now=NOW - timedelta(hours=4),
    )
    for hours_ago, claimed in ((2.5, 6), (1.5, 6), (0.5, 6)):
        await record_fanout_cycle(
            db,
            FanoutCycleTotals(
                events_claimed=claimed,
                events_processed=0,
                public_posts=0,
                skipped_live_recheck=claimed,
                recheck_reasons={"original_identity_missing": claimed},
            ),
            now=NOW - timedelta(hours=hours_ago),
        )


# ------------------------------------------------------------- record/load


def test_record_and_load_roundtrip_aggregates_reasons_by_label() -> None:
    async def run() -> None:
        db = await _db()
        await record_fanout_cycle(
            db,
            FanoutCycleTotals(
                events_claimed=10,
                events_processed=8,
                public_posts=2,
                skipped_duplicate=1,
                skipped_not_alertable=3,
                skipped_live_recheck=4,
                recheck_reasons={
                    "live_price_changed": 2,
                    "live_fetch_failed": 1,
                    "live_fetch_unverified": 1,
                },
            ),
            now=NOW,
        )
        health = await load_fanout_health(db, now=NOW)
        assert health.available
        assert health.events_claimed == 10
        assert health.public_posts == 2
        assert health.skipped_not_alertable == 3
        # The two fetch-failure reasons share one plain-words label.
        assert health.aggregated_reason_counts() == [
            ("Live price check couldn't be completed", 2),
            ("Price had already changed at the store", 2),
        ]
        summary = health.turnaway_summary()
        assert "Deals checked for posting: **10**" in summary
        assert "Price had already changed at the store" in summary
        await db.conn.close()

    asyncio.run(run())


def test_load_health_empty_database_is_unavailable_and_calm() -> None:
    async def run() -> None:
        db = await _db()
        health = await load_fanout_health(db, now=NOW)
        assert health.available is False
        assert "No turn-aways recorded yet" in health.turnaway_summary()
        # Recording and loading against a missing db never raise.
        await record_fanout_cycle(None, FanoutCycleTotals(public_posts=1))
        assert (await load_fanout_health(None)).available is False
        await db.conn.close()

    asyncio.run(run())


def test_record_prunes_history_beyond_retention() -> None:
    async def run() -> None:
        db = await _db()
        await record_fanout_cycle(
            db,
            FanoutCycleTotals(events_claimed=1),
            now=NOW - timedelta(hours=72),
        )
        await record_fanout_cycle(
            db,
            FanoutCycleTotals(events_claimed=2),
            now=NOW,
        )
        health = await load_fanout_health(db, window_hours=24 * 7, now=NOW)
        assert health.cycles == 1
        assert health.events_claimed == 2
        await db.conn.close()

    asyncio.run(run())


# ------------------------------------------------------- pure evaluation


def _snapshot(**overrides):
    base = dict(
        posts_in_window=0,
        claimed_in_window=18,
        enrolled_guilds=1,
        alarm_fired=False,
        alarm_attempt_at="",
        last_public_post_at=(NOW - timedelta(hours=5)).isoformat(),
        tracking_since=(NOW - timedelta(days=2)).isoformat(),
        reason_counts={"original_identity_missing": 18},
        now=NOW,
    )
    base.update(overrides)
    return evaluate_outage_snapshot(**base)


def test_evaluation_fires_on_the_incident_shape() -> None:
    decision = _snapshot()
    assert decision.should_fire
    assert decision.claimed_in_window == 18
    assert decision.posts_in_window == 0
    assert decision.top_reason == "Deal was missing its proof details"
    assert decision.top_reason_count == 18


def test_evaluation_quiet_when_posts_landed() -> None:
    assert not _snapshot(posts_in_window=1).should_fire


def test_evaluation_quiet_below_the_flow_floor() -> None:
    assert not _snapshot(claimed_in_window=9).should_fire


def test_evaluation_quiet_with_no_enrolled_guild() -> None:
    assert not _snapshot(enrolled_guilds=0).should_fire


def test_evaluation_quiet_before_the_window_fills() -> None:
    # Tracking only began an hour ago and no post has ever been seen:
    # there is not yet a full outage window to judge.
    decision = _snapshot(
        last_public_post_at="",
        tracking_since=(NOW - timedelta(hours=1)).isoformat(),
    )
    assert not decision.should_fire


def test_evaluation_quiet_once_fired_and_while_retry_is_fresh() -> None:
    assert not _snapshot(alarm_fired=True).should_fire
    assert not _snapshot(
        alarm_attempt_at=(NOW - timedelta(minutes=5)).isoformat()
    ).should_fire
    # A stale attempt (past the retry cadence) may try again.
    assert _snapshot(
        alarm_attempt_at=(NOW - timedelta(minutes=45)).isoformat()
    ).should_fire


# ------------------------------------------------- evaluation over the DB


def test_evaluate_outage_over_recorded_history() -> None:
    async def run() -> None:
        db = await _db()
        await _seed_incident(db)
        decision = await evaluate_outage(db, enrolled_guilds=1, now=NOW)
        assert decision.should_fire
        assert decision.claimed_in_window == 18
        assert decision.posts_in_window == 0
        assert decision.top_reason == "Deal was missing its proof details"
        # Nobody enrolled -> never fire, even on the incident shape.
        decision = await evaluate_outage(db, enrolled_guilds=0, now=NOW)
        assert not decision.should_fire
        await db.conn.close()

    asyncio.run(run())


def test_genuine_quiet_never_fires() -> None:
    async def run() -> None:
        db = await _db()
        # Cycles run, but no deals flow at all (a quiet night).
        for hours_ago in (2.5, 1.5, 0.5):
            await record_fanout_cycle(
                db,
                FanoutCycleTotals(events_claimed=0),
                now=NOW - timedelta(hours=hours_ago),
            )
        decision = await evaluate_outage(db, enrolled_guilds=1, now=NOW)
        assert not decision.should_fire
        await db.conn.close()

    asyncio.run(run())


# ------------------------------------------------------------- the alarm


def test_alarm_sends_once_per_episode_and_rearms_after_a_post() -> None:
    async def run() -> None:
        db = await _db()
        await _enroll_one_guild(db)
        await _seed_incident(db)
        user = FakeUser()
        bot = FakeBot(user)

        assert await maybe_send_outage_alarm(bot, db, now=NOW) is True
        assert len(user.sent) == 1
        message = user.sent[0]
        assert "deal posts look stuck" in message
        assert "**18** deals were checked" in message
        assert "Deal was missing its proof details" in message
        assert "/autoscan_health" in message
        # No internal jargon leaks into the DM.
        assert "fanout" not in message
        assert "walmart_fanout" not in message

        # Same episode: no second DM.
        assert await maybe_send_outage_alarm(bot, db, now=NOW) is False
        assert len(user.sent) == 1

        # A post lands -> the alarm re-arms. A fresh outage after it can
        # fire again.
        later = NOW + timedelta(hours=5)
        await record_fanout_cycle(
            db,
            FanoutCycleTotals(events_claimed=2, events_processed=2, public_posts=1),
            now=NOW + timedelta(hours=1),
        )
        for hours_ago in (2.0, 1.0, 0.25):
            await record_fanout_cycle(
                db,
                FanoutCycleTotals(
                    events_claimed=6,
                    skipped_live_recheck=6,
                    recheck_reasons={"live_price_changed": 6},
                ),
                now=later - timedelta(hours=hours_ago),
            )
        assert await maybe_send_outage_alarm(bot, db, now=later) is True
        assert len(user.sent) == 2
        assert "Price had already changed at the store" in user.sent[1]
        await db.conn.close()

    asyncio.run(run())


def test_alarm_dm_failure_never_propagates_and_does_not_consume_episode() -> None:
    async def run() -> None:
        db = await _db()
        await _enroll_one_guild(db)
        await _seed_incident(db)
        user = FakeUser(fail=True)
        bot = FakeBot(user)

        assert await maybe_send_outage_alarm(bot, db, now=NOW) is False
        health = await load_fanout_health(db, now=NOW)
        assert health.alarm_fired is False

        # After the retry cadence the alarm may try again and succeed.
        user.fail = False
        later = NOW + timedelta(minutes=45)
        assert await maybe_send_outage_alarm(bot, db, now=later) is True
        assert len(user.sent) == 1
        await db.conn.close()

    asyncio.run(run())


def test_alarm_stays_quiet_without_an_enrolled_guild() -> None:
    async def run() -> None:
        db = await _db()
        await ensure_public_alert_table(db)  # table exists, nobody enabled
        await _seed_incident(db)
        user = FakeUser()
        assert await maybe_send_outage_alarm(FakeBot(user), db, now=NOW) is False
        assert user.sent == []
        await db.conn.close()

    asyncio.run(run())


def test_resolve_owner_prefers_env_then_bot_owner_id(monkeypatch) -> None:
    async def run() -> None:
        bot = FakeBot(FakeUser(), owner_id=424242)
        monkeypatch.delenv("SNIPERPLUG_OWNER_USER_ID", raising=False)
        assert await resolve_owner_user_id(bot) == 424242
        monkeypatch.setenv("SNIPERPLUG_OWNER_USER_ID", "777")
        assert await resolve_owner_user_id(bot) == 777

    asyncio.run(run())


def test_alarm_message_is_plain_words() -> None:
    decision = _snapshot()
    message = build_outage_alarm_message(decision)
    assert "look stuck" in message
    assert "original_identity_missing" not in message
    assert "Deal was missing its proof details (18 deals)" in message


# ------------------------------------------------------- health embed field


@pytest.mark.asyncio
async def test_health_embed_shows_turnaway_reasons(tmp_path, monkeypatch):
    from sniperplug.cogs.canonical_public_alerts import (
        build_global_autoscan_health_embed,
    )
    from sniperplug.services.walmart_exact_verification_queue import (
        ensure_walmart_exact_verification_queue,
    )
    from walmart_pipeline_harness import Pipeline

    p = await Pipeline(tmp_path, monkeypatch).start()
    try:
        await p.enroll(p.guilds[0], threshold=40)
        await ensure_walmart_exact_verification_queue(p.db)

        # No fanout health recorded yet: the field is calm, not an error.
        embed = await build_global_autoscan_health_embed(p.bot, p.guilds[0].id)
        fields = {field.name: field.value for field in embed.fields}
        assert "Why deals were turned away (last 24 hours)" in fields
        assert "No turn-aways recorded yet" in fields["Why deals were turned away (last 24 hours)"]

        await record_fanout_cycle(
            p.db,
            FanoutCycleTotals(
                events_claimed=12,
                events_processed=9,
                public_posts=3,
                skipped_not_alertable=5,
                skipped_live_recheck=4,
                recheck_reasons={
                    "live_price_changed": 3,
                    "live_seller_name_changed": 1,
                },
            ),
        )
        embed = await build_global_autoscan_health_embed(p.bot, p.guilds[0].id)
        fields = {field.name: field.value for field in embed.fields}
        value = fields["Why deals were turned away (last 24 hours)"]
        assert "Deals checked for posting: **12**" in value
        assert "Price had already changed at the store: **3**" in value
        assert "Seller changed: **1**" in value
        assert "Below a server's minimum discount or outside its categories: **5**" in value
        # Raw reason keys never leak onto the staff screen.
        assert "live_price_changed" not in value
        assert "skipped_not_alertable" not in value
    finally:
        await p.close()
