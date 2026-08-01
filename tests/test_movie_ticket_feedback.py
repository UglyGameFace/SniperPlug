from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from sniperplug.services.movie_ticket_drops import (
    ATOM_SOURCE_KEY,
    MovieTicketDrop,
    MovieTicketStore,
)
from sniperplug.services.movie_ticket_feedback import (
    FAILED_VERDICT,
    WORKED_VERDICT,
    MovieTicketFeedbackCounts,
    MovieTicketFeedbackView,
    get_movie_ticket_feedback_counts,
    list_recent_movie_ticket_deliveries,
    record_movie_ticket_feedback,
)
from sniperplug.storage.db import Database


ROOT = Path(__file__).resolve().parents[1]
BOT_SOURCE = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
COG_SOURCE = (ROOT / "sniperplug/cogs/movie_ticket_feedback.py").read_text(encoding="utf-8")
SERVICE_SOURCE = (ROOT / "sniperplug/services/movie_ticket_feedback.py").read_text(encoding="utf-8")


def _drop() -> MovieTicketDrop:
    return MovieTicketDrop(
        drop_id="feedback-drop-001",
        source_key=ATOM_SOURCE_KEY,
        source_label="Official Atom Promotions Hub",
        title="Feedback Test Movie",
        code="MOVIEFREE",
        classification="public_reusable",
        ticket_limit=2,
        offer_url="https://www.atomtickets.com/movies/feedback-test/123",
        validity_text="While supplies last.",
        restrictions=("One time use per customer.",),
        raw_text="Enter promo code MOVIEFREE.",
    )


def test_runtime_registers_one_movie_ticket_feedback_cog() -> None:
    assert "from sniperplug.cogs.movie_ticket_feedback import MovieTicketFeedbackCog" in BOT_SOURCE
    assert BOT_SOURCE.count("await self.add_cog(MovieTicketFeedbackCog(self))") == 1
    assert "@commands.Cog.listener(\"on_message\")" in COG_SOURCE
    assert "MOVIE_DROP_EMBED_TITLE = \"🎟️ FREE ATOM TICKET DROP\"" in COG_SOURCE


def test_feedback_view_has_link_and_two_persistent_vote_buttons() -> None:
    bot = SimpleNamespace(db=None)
    view = MovieTicketFeedbackView(
        bot,
        drop_id="feedback-drop-001",
        offer_url="https://www.atomtickets.com/promotions",
        counts=MovieTicketFeedbackCounts(worked=4, failed=2),
    )

    assert view.timeout is None
    assert len(view.children) == 3
    labels = [str(getattr(item, "label", "")) for item in view.children]
    assert labels == ["Open official offer", "Worked · 4", "Didn’t Work · 2"]
    custom_ids = [str(getattr(item, "custom_id", "") or "") for item in view.children]
    assert any(value.startswith("movie_ticket_feedback:worked:") for value in custom_ids)
    assert any(value.startswith("movie_ticket_feedback:failed:") for value in custom_ids)
    assert all(len(value) <= 100 for value in custom_ids)


def test_demo_feedback_buttons_are_disabled() -> None:
    bot = SimpleNamespace(db=None)
    view = MovieTicketFeedbackView(
        bot,
        drop_id="movie-ticket-demo",
        offer_url="https://www.atomtickets.com/promotions",
        demo=True,
    )
    vote_buttons = [item for item in view.children if getattr(item, "custom_id", None)]
    assert vote_buttons
    assert all(item.disabled for item in vote_buttons)


def test_storage_enforces_one_vote_per_user_and_allows_vote_changes(tmp_path: Path) -> None:
    asyncio.run(_exercise_feedback_store(tmp_path / "movie-feedback.sqlite3"))


async def _exercise_feedback_store(path: Path) -> None:
    db = Database(str(path))
    await db.connect()
    await db.init()
    store = MovieTicketStore(db)
    await store.ensure_schema()

    try:
        drop = _drop()
        await store.replace_active_drops(ATOM_SOURCE_KEY, (drop,))

        first = await record_movie_ticket_feedback(
            db,
            drop_id=drop.drop_id,
            user_id=1001,
            guild_id=2001,
            verdict=WORKED_VERDICT,
        )
        assert first.applied is True
        assert first.duplicate is False
        assert first.changed_vote is False
        assert first.counts == MovieTicketFeedbackCounts(worked=1, failed=0)

        duplicate = await record_movie_ticket_feedback(
            db,
            drop_id=drop.drop_id,
            user_id=1001,
            guild_id=2001,
            verdict=WORKED_VERDICT,
        )
        assert duplicate.applied is False
        assert duplicate.duplicate is True
        assert duplicate.counts == MovieTicketFeedbackCounts(worked=1, failed=0)

        changed = await record_movie_ticket_feedback(
            db,
            drop_id=drop.drop_id,
            user_id=1001,
            guild_id=2001,
            verdict=FAILED_VERDICT,
        )
        assert changed.applied is True
        assert changed.changed_vote is True
        assert changed.counts == MovieTicketFeedbackCounts(worked=0, failed=1)

        second_user = await record_movie_ticket_feedback(
            db,
            drop_id=drop.drop_id,
            user_id=1002,
            guild_id=2002,
            verdict=WORKED_VERDICT,
        )
        assert second_user.counts == MovieTicketFeedbackCounts(worked=1, failed=1)
        assert await get_movie_ticket_feedback_counts(db, drop.drop_id) == MovieTicketFeedbackCounts(worked=1, failed=1)

        await store.reserve_delivery(guild_id=2001, drop_id=drop.drop_id, channel_id=3001)
        await store.mark_delivery_sent(
            guild_id=2001,
            drop_id=drop.drop_id,
            channel_id=3001,
            message_id=4001,
        )
        deliveries = await list_recent_movie_ticket_deliveries(db)
        assert deliveries[0].drop_id == drop.drop_id
        assert deliveries[0].message_id == 4001
    finally:
        await db.close()


def test_feedback_cog_upgrades_existing_posts_and_fails_visibly() -> None:
    assert "list_recent_movie_ticket_deliveries" in COG_SOURCE
    assert "await message.edit(view=view)" in COG_SOURCE
    assert "register_persistent_movie_ticket_feedback_views" in COG_SOURCE
    assert "await interaction.response.defer(ephemeral=True)" in SERVICE_SOURCE
    assert "Duplicate vote ignored" in SERVICE_SOURCE
    assert "Updated your report" in SERVICE_SOURCE
    assert "could not be saved" in SERVICE_SOURCE
