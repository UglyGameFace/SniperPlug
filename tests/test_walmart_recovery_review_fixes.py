from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord

from sniperplug.services import walmart_delivery_recovery as recovery


def _item(*, current_price: float = 80.0) -> recovery.WalmartRecoveryItem:
    card = SimpleNamespace(
        label="Example Walmart item",
        retailer="walmart",
        public_post_key="walmart:123:price:80.00",
        current_price=current_price,
        api_current_price=current_price,
        api_reference_price=100.0,
        api_discount_percent=20.0,
        discount=20.0,
        score=120,
        should_alert=True,
        url="https://www.walmart.com/ip/123",
        selected_offer_id="offer-123",
        sku="123",
        upc=None,
        embed=discord.Embed(title="Example Walmart item"),
    )
    return recovery.WalmartRecoveryItem(
        deal_key="event:123",
        public_key="walmart:123:price:80.00",
        label="Example Walmart item",
        event_at="2026-08-05T00:00:00+00:00",
        outcome="below_threshold",
        detail="below threshold",
        discount=20.0,
        threshold=40,
        item_id="123",
        product_url=card.url,
        last_error="",
        post_status="",
        candidate=SimpleNamespace(product_id="123"),
        card=card,
    )


def _install_override_dependencies(
    monkeypatch,
    *,
    recent_price: float,
    send_allowed: bool,
):
    reserved: list[str] = []
    released: list[str] = []
    sent: list[bool] = []

    class Channel:
        id = 555

        async def send(self, **_kwargs):
            if not send_allowed:
                raise AssertionError("duplicate guard should block before channel.send")
            sent.append(True)
            return SimpleNamespace(id=777)

    async def config(*_args, **_kwargs):
        return {
            "enabled": True,
            "channel_id": 555,
            "retailers": ("walmart",),
        }

    async def resolve(*_args, **_kwargs):
        return Channel(), None

    async def load_state(*_args, **_kwargs):
        return "", ""

    async def reserve(_db, *, deal_key, **_kwargs):
        reserved.append(str(deal_key))
        return True

    async def release(_db, *, deal_key, **_kwargs):
        released.append(str(deal_key))

    async def recent(*_args, **_kwargs):
        return {"current_price": recent_price}

    async def no_op(*_args, **_kwargs):
        return None

    async def feedback(*_args, **_kwargs):
        return None

    async def finalized(*_args, **_kwargs):
        return True, []

    monkeypatch.setattr(recovery, "is_public_deal_candidate", lambda *_a, **_k: True)
    monkeypatch.setattr(recovery, "get_public_alert_config", config)
    monkeypatch.setattr(recovery, "resolve_public_alert_channel", resolve)
    monkeypatch.setattr(recovery, "_load_public_post_state", load_state)
    monkeypatch.setattr(recovery, "reserve_public_deal_post", reserve)
    monkeypatch.setattr(recovery, "release_public_deal_reservation", release)
    monkeypatch.setattr(recovery, "safe_find_recent_alert", recent)
    monkeypatch.setattr(recovery, "mark_public_deal_sending", no_op)
    monkeypatch.setattr(recovery, "build_feedback_target", lambda *_a, **_k: object())
    monkeypatch.setattr(recovery, "build_deal_feedback_view", feedback)
    monkeypatch.setattr(recovery, "finalize_successful_public_post", finalized)
    monkeypatch.setattr(recovery, "_mark_original_delivery_receipt", no_op)
    monkeypatch.setattr(recovery, "_record_action", no_op)

    return reserved, released, sent


def test_owner_override_blocks_same_price_recent_alert_and_releases_both_locks(
    monkeypatch,
) -> None:
    reserved, released, sent = _install_override_dependencies(
        monkeypatch,
        recent_price=80.0,
        send_allowed=False,
    )

    result = asyncio.run(
        recovery.post_walmart_owner_override(
            bot=SimpleNamespace(db=object()),
            guild_id=1,
            item=_item(current_price=80.0),
            actor_id=2,
        )
    )

    assert result.ok is False
    assert "normal 30-day alert dedupe" in result.message
    assert "same or higher price" in result.message
    assert len(reserved) == 2
    assert set(released) == set(reserved)
    assert sent == []


def test_owner_override_allows_a_genuinely_lower_verified_price(monkeypatch) -> None:
    reserved, released, sent = _install_override_dependencies(
        monkeypatch,
        recent_price=90.0,
        send_allowed=True,
    )

    result = asyncio.run(
        recovery.post_walmart_owner_override(
            bot=SimpleNamespace(db=object()),
            guild_id=1,
            item=_item(current_price=80.0),
            actor_id=2,
        )
    )

    assert result.ok is True
    assert "normal recent-alert dedupe still passed" in result.message
    assert len(reserved) == 2
    assert released == []
    assert sent == [True]


def test_exact_recheck_preserves_an_active_worker_lease(monkeypatch) -> None:
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

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
            text = str(sql)
            self.sql.append(text)
            self.params.append(tuple(params))
            if "SELECT status, next_attempt_at, lease_until" in text:
                return Cursor(
                    {
                        "status": "verifying",
                        "next_attempt_at": "later",
                        "lease_until": future,
                    }
                )
            return Cursor()

        async def commit(self):
            self.commits += 1

    conn = Connection()
    db = SimpleNamespace(require_conn=lambda: conn)
    actions: list[str] = []

    async def no_op(*_args, **_kwargs):
        return None

    async def record(*_args, outcome, **_kwargs):
        actions.append(str(outcome))

    monkeypatch.setattr(recovery, "ensure_walmart_exact_verification_queue", no_op)
    monkeypatch.setattr(recovery, "_record_action", record)

    item = _item()
    item = recovery.WalmartRecoveryItem(
        **{
            **item.__dict__,
            "outcome": "exact_identity_blocked",
            "card": None,
        }
    )
    result = asyncio.run(
        recovery.recheck_walmart_exact_offer(
            db=db,
            guild_id=1,
            item=item,
            actor_id=2,
        )
    )

    assert result.ok is False
    assert "already being verified" in result.message
    update_sql = next(sql for sql in conn.sql if "UPDATE walmart_exact_detail_queue" in sql)
    assert "lease_until IS NULL" in update_sql
    assert "lease_until = ''" in update_sql
    assert "lease_until < ?" in update_sql
    assert actions == ["already_running"]
    assert conn.commits == 1
