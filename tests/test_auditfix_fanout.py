"""Audit fanout fix, item 3 (2026-11-17): feedback learning on live delivery.

``apply_feedback_learning_to_cards`` used to run only in the legacy/native
autoscan cogs, so thumbs-up/down votes were theatre on the live
global-catalog fanout. The bulk fanout must apply the guild's saved
feedback to each guild's copy of a card before public posting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import discord
import pytest

from sniperplug.services import walmart_global_deal_fanout_bulk as bulk
from sniperplug.services.autoscan_live_guild_reconciliation import (
    LiveAutoScanGuild,
    LiveGuildLoadResult,
)
from sniperplug.services.deal_feedback import (
    ensure_deal_feedback_tables,
    feedback_product_key,
)


# ---------------------------------------------------------------- item 3


def _feedback_embed_fields(card: Any) -> list[str]:
    embed = getattr(card, "embed", None)
    if embed is None:
        return []
    return [str(field.name or "") for field in embed.fields]


@pytest.mark.asyncio
async def test_bulk_fanout_applies_guild_feedback_to_public_card(monkeypatch) -> None:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = SimpleNamespace(require_conn=lambda: conn)
    try:
        await ensure_deal_feedback_tables(db)
        card = SimpleNamespace(
            retailer="walmart",
            label="Samsung Galaxy S24 FE",
            title="Samsung Galaxy S24 FE",
            url="https://www.walmart.com/ip/29500011",
            direct_product_url="https://www.walmart.com/ip/29500011",
            selected_offer_id="offer-1",
            sku="29500011",
            score=80,
            discount=45.0,
            current_price=55.0,
            api_current_price=55.0,
            embed=discord.Embed(title="Samsung Galaxy S24 FE"),
        )
        # One "bad pick" vote: total_score -2 -> product adjustment -10.
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "INSERT INTO guild_deal_feedback_summary "
            "(guild_id, target_key, retailer, title, url, brand_hint, "
            "good_count, bad_count, bad_brand_count, flip_count, weak_count, "
            "total_score, last_action_at) VALUES (?, ?, ?, ?, ?, ?, 0, 1, 0, 0, 0, -2, ?)",
            (
                111,
                feedback_product_key(card, retailer="walmart"),
                "walmart",
                "Samsung Galaxy S24 FE",
                "https://www.walmart.com/ip/29500011",
                "samsung",
                now,
            ),
        )
        await conn.commit()

        posted_cards: list = []
        finalized: list = []

        async def fake_post(**kwargs):
            posted_cards.extend(kwargs.get("cards", ()))
            return SimpleNamespace(
                posted=1,
                skipped_recent_alert_duplicate=0,
                skipped_reserved_duplicate=0,
                skipped_not_alertable=0,
                skipped_disabled=0,
                skipped_wrong_retailer=0,
                skipped_duplicate=False,
                errors=(),
            )

        async def fake_live(db_arg, bot_arg, *, required_retailer):
            return LiveGuildLoadResult(guilds=(LiveAutoScanGuild(111, 999),))

        async def _fake_ingest(_db, *, limit):
            return bulk._IngestResult(loaded=1, exact_cards=1, new_events=1)

        async def _fake_claim(_db, *, limit):
            return [
                bulk._ClaimedEvent(
                    deal_key="walmart:offer-1",
                    claim_token="tok-1",
                    card=card,
                )
            ]

        async def _fake_superseded(_db, _events):
            return {}

        async def _fake_enrolled(_db, _bot, _result):
            return []

        async def _fake_no_prefs(_db):
            return []

        async def _fake_no_wishlists(_db, _ids):
            return {}

        async def _fake_record_decisions(_db, _decisions):
            return None

        async def _fake_finalize(_db, *, processed, failed):
            finalized.extend(processed)

        async def _fake_defer(_db, _deferred):
            return None

        monkeypatch.setattr(bulk, "ensure_global_deal_event_tables_once", lambda _db: _noop())
        monkeypatch.setattr(bulk, "_ingest_verified_queue_events_bulk", _fake_ingest)
        monkeypatch.setattr(bulk, "_claim_pending_events_bulk", _fake_claim)
        monkeypatch.setattr(bulk, "_superseded_event_keys", _fake_superseded)
        monkeypatch.setattr(bulk, "_enrolled_walmart_guilds_not_live", _fake_enrolled)
        monkeypatch.setattr(bulk, "list_live_public_alert_guilds", fake_live)
        monkeypatch.setattr(bulk, "get_starting_deal_percent", lambda _db, _gid: _as_40())
        monkeypatch.setattr(bulk, "maybe_post_public_deal_cards", fake_post)
        monkeypatch.setattr(bulk, "list_enabled_dm_deal_alert_preferences", _fake_no_prefs)
        monkeypatch.setattr(bulk, "list_wishlists_for_users", _fake_no_wishlists)
        monkeypatch.setattr(bulk, "record_walmart_fanout_decisions", _fake_record_decisions)
        monkeypatch.setattr(bulk, "_finalize_claimed_events_bulk", _fake_finalize)
        monkeypatch.setattr(bulk, "_defer_claimed_events_bulk", _fake_defer)

        bot = SimpleNamespace(db=db, get_user=None, fetch_user=None)
        result = await bulk.fanout_recent_exact_walmart_deals(bot)

        assert result.public_posts == 1
        assert len(posted_cards) == 1
        posted = posted_cards[0]
        # The guild's downvote reaches the delivered card.
        assert int(getattr(posted, "feedback_learning_score", 0) or 0) == -10
        fields = _feedback_embed_fields(posted)
        assert "🧠 Feedback learning" in fields
        assert any(
            "feedback penalty -10" in str(field.value or "")
            for field in posted.embed.fields
            if str(field.name or "") == "🧠 Feedback learning"
        )
        # Per-guild isolation: the shared event card carries no other
        # guild's annotation — the next guild gets its own copy.
        assert int(getattr(card, "feedback_learning_score", 0) or 0) == 0
        assert "🧠 Feedback learning" not in _feedback_embed_fields(card)
    finally:
        await conn.close()


async def _noop() -> None:
    return None


async def _as_40() -> int:
    return 40
