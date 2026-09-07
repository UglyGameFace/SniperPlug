from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sniperplug.cogs import registered_multi_source_movies as runtime
from sniperplug.services.gofobo_screenings import GOFOBO_SOURCE_KEY
from sniperplug.services.movie_ticket_drops import (
    ATOM_SOURCE_KEY,
    MovieTicketDrop,
)
from sniperplug.services.movie_ticket_feedback import (
    WORKED_RESULT,
    MovieTicketFeedbackStore,
)
from sniperplug.services.movie_ticket_recovery import (
    MOVIE_SOURCE_CACHE_MAX_AGE_SECONDS,
    movie_source_health_text,
    parse_current_atom_promotions_html,
)
from sniperplug.services.movie_ticket_snowflake_store import SnowflakeSafeMovieTicketStore
from sniperplug.storage.db import Database


GUILD_ID = 1514374173517152418
CHANNEL_ID = 1530990344404205792
MESSAGE_ID = 1530990344404205888
USER_ID = 1530990344404205999

PARTNER_ONLY_ATOM_HTML = """
<!doctype html>
<html>
<head><title>Atom Tickets Promotions | Promo Hub</title></head>
<body>
  <h2>Atom Tickets Promotions</h2>
  <h3>Partner Promotions</h3>
  <h3>Samsung Wallet Customers - Get $10 off movie tickets on Atom</h3>
  <ul>
    <li>Use the promotional code given directly to you through Samsung Wallet.</li>
    <li>Enter the promo code given to you in the Promo Code field at checkout.</li>
  </ul>
</body>
</html>
"""


def _drop(
    *,
    drop_id: str,
    source_key: str = ATOM_SOURCE_KEY,
    classification: str = "public_reusable",
    code: str = "FRESHMOVIE",
    title: str = "Fresh Movie",
) -> MovieTicketDrop:
    return MovieTicketDrop(
        drop_id=drop_id,
        source_key=source_key,
        source_label="Official movie source",
        title=title,
        code=code,
        classification=classification,
        ticket_limit=1,
        offer_url="https://www.atomtickets.com/promotions" if source_key == ATOM_SOURCE_KEY else "https://gofobo.com/MUTINY",
        validity_text="While supplies last.",
        restrictions=("Official source restrictions apply.",),
        raw_text="fixture",
    )


def test_current_partner_only_atom_page_is_valid_zero_public_codes() -> None:
    parsed = parse_current_atom_promotions_html(PARTNER_ONLY_ATOM_HTML)

    assert parsed.document_valid is True
    assert parsed.film_section_found is False
    assert parsed.partner_section_found is True
    assert parsed.drops == ()


def test_atom_access_block_page_is_never_accepted_as_empty_success() -> None:
    blocked = PARTNER_ONLY_ATOM_HTML.replace(
        "<body>",
        "<body><h1>Access Denied</h1><p>Verify you are human</p>",
    )
    parsed = parse_current_atom_promotions_html(blocked)

    assert parsed.document_valid is False
    assert parsed.drops == ()


def test_production_store_hides_stale_rows_but_keeps_durable_304_cache(tmp_path: Path) -> None:
    asyncio.run(_exercise_stale_cache(tmp_path / "movie-stale.sqlite3"))


async def _exercise_stale_cache(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.init()
    store = SnowflakeSafeMovieTicketStore(db)
    try:
        drop = _drop(drop_id="atom-stale")
        await store.replace_active_drops(ATOM_SOURCE_KEY, (drop,))
        await store.record_source_success(
            source_key=ATOM_SOURCE_KEY,
            etag='"old"',
            last_modified="",
            active_drop_count=1,
        )
        assert [item.drop_id for item in await store.list_active_drops()] == [drop.drop_id]

        stale_at = (
            datetime.now(UTC)
            - timedelta(seconds=MOVIE_SOURCE_CACHE_MAX_AGE_SECONDS + 60)
        ).isoformat()
        conn = db.require_conn()
        await conn.execute(
            """
            UPDATE movie_ticket_source_state
            SET last_success_at = ?, last_error = 'Official Atom promotions page returned HTTP 403.'
            WHERE source_key = ?
            """,
            (stale_at, ATOM_SOURCE_KEY),
        )
        await conn.execute(
            "UPDATE movie_ticket_drops SET last_seen_at = ? WHERE drop_id = ?",
            (stale_at, drop.drop_id),
        )
        await conn.commit()

        assert await store.list_active_drops() == []
        durable = await store.list_active_drops(include_stale=True)
        assert [item.drop_id for item in durable] == [drop.drop_id]

        # The registered 304 path deliberately reads durable rows. A successful
        # 304 then records a fresh source success and makes them public again.
        cog = object.__new__(runtime.MovieTicketsCog)
        cog.store = store
        raw = await cog._list_source_drops(ATOM_SOURCE_KEY)
        assert [item.drop_id for item in raw] == [drop.drop_id]
    finally:
        await db.close()


def test_gofobo_local_screening_survives_production_store(tmp_path: Path) -> None:
    asyncio.run(_exercise_gofobo_store(tmp_path / "movie-gofobo.sqlite3"))


async def _exercise_gofobo_store(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.init()
    store = SnowflakeSafeMovieTicketStore(db)
    try:
        screening = _drop(
            drop_id="gofobo-local",
            source_key=GOFOBO_SOURCE_KEY,
            classification="local_screening",
            code="",
            title="Local Screening",
        )
        await store.replace_active_drops(GOFOBO_SOURCE_KEY, (screening,))
        await store.record_source_success(
            source_key=GOFOBO_SOURCE_KEY,
            etag="",
            last_modified="",
            active_drop_count=1,
        )

        active = await store.list_active_drops()
        assert [(item.drop_id, item.classification, item.code) for item in active] == [
            ("gofobo-local", "local_screening", ""),
        ]
    finally:
        await db.close()


def test_movie_delivery_and_feedback_use_exact_text_snowflakes(tmp_path: Path) -> None:
    asyncio.run(_exercise_exact_delivery_feedback(tmp_path / "movie-exact-ids.sqlite3"))


async def _exercise_exact_delivery_feedback(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.init()
    ticket_store = SnowflakeSafeMovieTicketStore(db)
    feedback_store = MovieTicketFeedbackStore(db)
    try:
        drop = _drop(drop_id="exact-id-drop")
        await ticket_store.replace_active_drops(ATOM_SOURCE_KEY, (drop,))
        await ticket_store.record_source_success(
            source_key=ATOM_SOURCE_KEY,
            etag="",
            last_modified="",
            active_drop_count=1,
        )

        assert await ticket_store.reserve_delivery(
            guild_id=GUILD_ID,
            drop_id=drop.drop_id,
            channel_id=CHANNEL_ID,
        ) is True
        await ticket_store.mark_delivery_sent(
            guild_id=GUILD_ID,
            drop_id=drop.drop_id,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
        )

        context = await feedback_store.resolve_message(
            guild_id=GUILD_ID,
            message_id=MESSAGE_ID,
        )
        assert context is not None
        assert context.guild_id == GUILD_ID
        assert context.channel_id == CHANNEL_ID
        assert context.message_id == MESSAGE_ID

        vote = await feedback_store.record_vote(
            guild_id=GUILD_ID,
            drop_id=drop.drop_id,
            user_id=USER_ID,
            result=WORKED_RESULT,
        )
        assert vote.counts.worked == 1
        assert await ticket_store.count_sent_for_guild(GUILD_ID) == 1

        conn = db.require_conn()
        delivery_cursor = await conn.execute(
            """
            SELECT typeof(guild_id) AS guild_type,
                   typeof(channel_id) AS channel_type,
                   typeof(message_id) AS message_type,
                   guild_id, channel_id, message_id
            FROM movie_ticket_deliveries_v2
            WHERE drop_id = ?
            """,
            (drop.drop_id,),
        )
        delivery = await delivery_cursor.fetchone()
        assert delivery["guild_type"] == "text"
        assert delivery["channel_type"] == "text"
        assert delivery["message_type"] == "text"
        assert delivery["guild_id"] == str(GUILD_ID)
        assert delivery["channel_id"] == str(CHANNEL_ID)
        assert delivery["message_id"] == str(MESSAGE_ID)

        feedback_cursor = await conn.execute(
            """
            SELECT typeof(guild_id) AS guild_type,
                   typeof(user_id) AS user_type,
                   guild_id, user_id
            FROM movie_ticket_feedback_v2
            WHERE drop_id = ?
            """,
            (drop.drop_id,),
        )
        feedback = await feedback_cursor.fetchone()
        assert feedback["guild_type"] == "text"
        assert feedback["user_type"] == "text"
        assert feedback["guild_id"] == str(GUILD_ID)
        assert feedback["user_id"] == str(USER_ID)
    finally:
        await db.close()


def test_source_health_marks_old_failed_cache_hidden() -> None:
    stale_at = (
        datetime.now(UTC)
        - timedelta(seconds=MOVIE_SOURCE_CACHE_MAX_AGE_SECONDS + 60)
    ).isoformat()
    from sniperplug.services.movie_ticket_drops import MovieTicketSourceState

    line = movie_source_health_text(
        "Atom",
        MovieTicketSourceState(
            source_key=ATOM_SOURCE_KEY,
            last_success_at=stale_at,
            last_error="Official Atom promotions page returned HTTP 403.",
        ),
        official_url="https://www.atomtickets.com/promotions",
    )

    assert "Stale/unavailable" in line
    assert "cached results hidden" in line
    assert "HTTP 403" in line


def test_registered_runtime_explains_setup_cannot_bypass_access_blocks() -> None:
    source = Path("sniperplug/cogs/registered_multi_source_movies.py").read_text(
        encoding="utf-8"
    )
    assert "rerunning setup cannot bypass a source site's 403/429/access block" in source
    assert "Old cached offers are intentionally hidden" in source
    assert "include_stale=True" in source
