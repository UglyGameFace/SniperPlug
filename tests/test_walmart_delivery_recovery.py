from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from sniperplug.services import walmart_delivery_recovery as recovery


def make_item(*, outcome: str, card=object(), item_id: str = "123") -> recovery.WalmartRecoveryItem:
    return recovery.WalmartRecoveryItem(
        deal_key="event:123",
        public_key="walmart:123:price:80.00",
        label="Example Walmart item",
        event_at="2026-08-05T00:00:00+00:00",
        outcome=outcome,
        detail="test reason",
        discount=20.0,
        threshold=40,
        item_id=item_id,
        last_error="",
        post_status="",
        candidate=SimpleNamespace(product_id=item_id),
        card=card,
    )


def test_recovery_action_boundaries_keep_hard_proof_failures_separate() -> None:
    below = make_item(outcome="below_threshold")
    assert below.can_owner_override is True
    assert below.can_share_manual_lead is False

    muted = make_item(outcome="category_muted")
    assert muted.can_owner_override is True

    quality = make_item(outcome="quality_blocked")
    assert quality.can_owner_override is False
    assert quality.can_recheck_exact is True
    assert quality.can_share_manual_lead is True

    pending = make_item(outcome="pending")
    assert pending.can_retry_current_rules is True
    assert pending.can_owner_override is False


def test_owner_override_refuses_card_that_fails_exact_public_proof(monkeypatch) -> None:
    item = make_item(outcome="below_threshold")
    monkeypatch.setattr(recovery, "is_public_deal_candidate", lambda *_args, **_kwargs: False)
    bot = SimpleNamespace(db=object())

    result = asyncio.run(
        recovery.post_walmart_owner_override(
            bot=bot,
            guild_id=1,
            item=item,
            actor_id=2,
        )
    )

    assert result.ok is False
    assert "cannot be called a verified deal" in result.message


def test_recheck_rearms_terminal_queue_row_without_erasing_history(monkeypatch) -> None:
    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.sql: list[str] = []
            self.params: list[tuple] = []
            self.commits = 0

        async def execute(self, sql, params=()):
            self.sql.append(str(sql))
            self.params.append(tuple(params))
            if "SELECT status, next_attempt_at" in str(sql):
                return Cursor({"status": "pending", "next_attempt_at": params[0]})
            return Cursor()

        async def commit(self):
            self.commits += 1

    conn = Connection()
    db = SimpleNamespace(require_conn=lambda: conn)

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(recovery, "ensure_walmart_exact_verification_queue", no_op)
    monkeypatch.setattr(recovery, "_record_action", no_op)

    result = asyncio.run(
        recovery.recheck_walmart_exact_offer(
            db=db,
            guild_id=10,
            item=make_item(outcome="quality_blocked", item_id="123"),
            actor_id=20,
        )
    )

    assert result.ok is True
    update_sql = next(sql for sql in conn.sql if "UPDATE walmart_exact_detail_queue" in sql)
    assert "status = 'pending'" in update_sql
    assert "next_attempt_at = ?" in update_sql
    assert "lease_token = ''" in update_sql
    assert "attempt_count" not in update_sql
    assert conn.commits == 1


def test_loader_classifies_current_threshold_instead_of_discovery_floor(monkeypatch) -> None:
    event_row = {
        "deal_key": "event:123",
        "snapshot_json": "{}",
        "first_seen_at": "2026-08-05T00:00:00+00:00",
        "source_verified_at": "2026-08-05T00:00:00+00:00",
        "processed_at": "2026-08-05T00:01:00+00:00",
        "last_error": "",
    }

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class Connection:
        async def execute(self, sql, _params=()):
            if "FROM walmart_global_exact_deal_events" in str(sql):
                return Cursor([event_row])
            if "FROM guild_public_deal_posts" in str(sql):
                return Cursor([])
            raise AssertionError(f"Unexpected SQL: {sql}")

    db = SimpleNamespace(require_conn=lambda: Connection())
    candidate = SimpleNamespace(product_id="123", variant_attributes={})
    card = SimpleNamespace(
        label="20 percent markdown",
        retailer="walmart",
        public_post_key="walmart:123:price:80.00",
        current_price=80.0,
        api_current_price=80.0,
        api_reference_price=100.0,
        api_discount_percent=20.0,
        discount=20.0,
        url="https://www.walmart.com/ip/123",
        embed=discord.Embed(title="Example"),
    )

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(recovery.legacy, "ensure_global_deal_event_tables", no_op)
    monkeypatch.setattr(recovery, "ensure_public_post_tables", no_op)
    monkeypatch.setattr(recovery, "_candidate_from_snapshot", lambda _value: candidate)
    monkeypatch.setattr(recovery.legacy, "_exact_card_for_candidate", lambda _candidate: card)
    monkeypatch.setattr(
        recovery,
        "decide_category",
        lambda *_args, **_kwargs: SimpleNamespace(action="normal", category_label="Other"),
    )
    monkeypatch.setattr(recovery, "is_public_deal_candidate", lambda *_args, **_kwargs: True)

    items = asyncio.run(
        recovery.load_walmart_recovery_items(
            db,
            guild_id=99,
            threshold=40,
            category_preferences={},
        )
    )

    assert len(items) == 1
    assert items[0].outcome == "below_threshold"
    assert items[0].discount == 20.0
    assert items[0].threshold == 40
