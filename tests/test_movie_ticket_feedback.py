from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from sniperplug.cogs.movie_ticket_feedback import (
    MOVIE_FAILED_ID,
    MOVIE_WORKED_ID,
    MovieTicketFeedbackView,
    feedback_confirmation,
)
from sniperplug.services.movie_ticket_drops import (
    ATOM_SOURCE_KEY,
    MovieTicketDrop,
    MovieTicketStore,
)
from sniperplug.services.movie_ticket_feedback import (
    FAILED_RESULT,
    WORKED_RESULT,
    MovieTicketFeedbackCounts,
    MovieTicketFeedbackStore,
)
from sniperplug.storage.db import Database


def _drop() -> MovieTicketDrop:
    return MovieTicketDrop(
        drop_id="drop-feedback-test",
        source_key=ATOM_SOURCE_KEY,
        source_label="Official Atom Promotions Hub",
        title="Feedback Test Movie",
        code="FEEDBACKFREE",
        classification="public_reusable",
        ticket_limit=2,
        offer_url="https://www.atomtickets.com/movies/feedback-test/123",
        validity_text="While supplies last.",
        restrictions=("One time use per customer.",),
        raw_text="Enter promo code FEEDBACKFREE during checkout.",
    )


def test_feedback_store_is_one_vote_per_user_and_allows_corrections(tmp_path: Path) -> None:
    asyncio.run(_exercise_feedback_store(tmp_path / "movie-feedback.sqlite3"))


async def _exercise_feedback_store(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.init()
    ticket_store = MovieTicketStore(db)
    feedback_store = MovieTicketFeedbackStore(db)

    try:
        await ticket_store.ensure_schema()
        drop = _drop()
        await ticket_store.replace_active_drops(ATOM_SOURCE_KEY, (drop,))
        assert await ticket_store.reserve_delivery(
            guild_id=101,
            drop_id=drop.drop_id,
            channel_id=202,
        ) is True
        await ticket_store.mark_delivery_sent(
            guild_id=101,
            drop_id=drop.drop_id,
            channel_id=202,
            message_id=303,
        )

        await feedback_store.ensure_schema()
        context = await feedback_store.resolve_message(guild_id=101, message_id=303)
        assert context is not None
        assert context.drop_id == drop.drop_id
        assert context.offer_url == drop.offer_url
        assert await feedback_store.resolve_message(guild_id=999, message_id=303) is None

        first = await feedback_store.record_vote(
            guild_id=101,
            drop_id=drop.drop_id,
            user_id=1,
            result=WORKED_RESULT,
        )
        assert first.previous_result == ""
        assert first.counts == MovieTicketFeedbackCounts(worked=1, failed=0)

        repeated = await feedback_store.record_vote(
            guild_id=101,
            drop_id=drop.drop_id,
            user_id=1,
            result=WORKED_RESULT,
        )
        assert repeated.repeated is True
        assert repeated.counts == MovieTicketFeedbackCounts(worked=1, failed=0)

        second_user = await feedback_store.record_vote(
            guild_id=101,
            drop_id=drop.drop_id,
            user_id=2,
            result=FAILED_RESULT,
        )
        assert second_user.counts == MovieTicketFeedbackCounts(worked=1, failed=1)

        corrected = await feedback_store.record_vote(
            guild_id=101,
            drop_id=drop.drop_id,
            user_id=1,
            result=FAILED_RESULT,
        )
        assert corrected.changed is True
        assert corrected.counts == MovieTicketFeedbackCounts(worked=0, failed=2)

        recent = await feedback_store.list_recent_deliveries(limit=10)
        assert [(item.guild_id, item.message_id) for item in recent] == [(101, 303)]

        with pytest.raises(ValueError):
            await feedback_store.record_vote(
                guild_id=101,
                drop_id=drop.drop_id,
                user_id=3,
                result="maybe",
            )
    finally:
        await db.close()


def test_feedback_view_is_persistent_and_shows_live_counts() -> None:
    asyncio.run(_exercise_feedback_view())


async def _exercise_feedback_view() -> None:
    cog = SimpleNamespace(record_feedback=None)
    view = MovieTicketFeedbackView(
        cog,
        offer_url="https://www.atomtickets.com/movies/example/123",
        counts=MovieTicketFeedbackCounts(worked=4, failed=2),
    )

    assert view.timeout is None
    by_id = {
        getattr(child, "custom_id", None): child
        for child in view.children
        if getattr(child, "custom_id", None)
    }
    assert by_id[MOVIE_WORKED_ID].label == "Worked (4)"
    assert by_id[MOVIE_FAILED_ID].label == "Didn't work (2)"
    assert any(
        getattr(child, "url", None) == "https://www.atomtickets.com/movies/example/123"
        for child in view.children
    )


def test_feedback_confirmation_explains_counts_and_no_auto_disable() -> None:
    from sniperplug.services.movie_ticket_feedback import MovieTicketFeedbackResult

    text = feedback_confirmation(
        MovieTicketFeedbackResult(
            previous_result=WORKED_RESULT,
            current_result=FAILED_RESULT,
            counts=MovieTicketFeedbackCounts(worked=3, failed=5),
        )
    )

    assert "changed" in text.lower()
    assert "3 worked" in text
    assert "5 didn't work" in text
    assert "do not automatically disable" in text
