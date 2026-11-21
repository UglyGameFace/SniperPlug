"""Audit fanout fix, item 15 (2026-11-17): extreme DMs decoupled from the guild public-lane opt-in.

Extreme DMs used to fire only when >=1 guild had opted into the *public*
extreme lane. A user who opted into DMs in a guild that never opted in got
nothing. The DM opt-in is sufficient on its own; the public opt-in gates
only public posting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services import walmart_extreme_price_fanout as extreme
from sniperplug.services.autoscan_live_guild_reconciliation import (
    LiveAutoScanGuild,
    LiveGuildLoadResult,
)
from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    save_dm_deal_alert_preference,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_snapshot,
    ensure_walmart_exact_verification_queue,
)


# ---------------------------------------------------------------- item 15


def _samsung_candidate(*, price: float = 1.0) -> SourceCandidate:
    item_id = "29500011"
    return SourceCandidate(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title="(Open Box) Samsung Galaxy S24 FE Graphite 128 GB DEMO",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=price,
        api_current_price=price,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id="offer-open-box-1",
        seller_name="Walmart",
        condition="Open Box",
        fulfillment_type="shipping",
        stock_status="In stock",
        can_add_to_cart=True,
        variant_label="Graphite 128 GB",
        variant_attributes={
            "sellerId": "walmart",
            "seller": "Walmart",
            "walmartSeller": "yes",
            "exactDetailItemId": item_id,
            "exactDetailPriceProof": "yes",
        },
    )


def _preference(*, categories=(), **kwargs) -> DmDealAlertPreference:
    return DmDealAlertPreference(
        user_id=7,
        enabled=True,
        min_discount=20,
        categories=categories,
        **kwargs,
    )


class _FakeUser:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=1)


@pytest.fixture(autouse=True)
def _fanout_unit_price_recheck(monkeypatch):
    """Unit fanout uses static proven snapshots; live-API behavior has dedicated tests."""

    async def _verified(_candidate, *, page_price=False):
        return None

    monkeypatch.setattr(extreme, "live_extreme_offer_rejection_reason", _verified)


async def _run_extreme_fanout(
    *,
    monkeypatch,
    opt_in_guild: bool,
    live_guilds: tuple = (LiveAutoScanGuild(295, 123),),
):
    from sniperplug.services import dm_deal_alerts as alerts
    from sniperplug.services import dm_delivery_policy as policy

    alerts._SCHEMA_READY = False
    policy._SCHEMA_READY = False

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = SimpleNamespace(require_conn=lambda: conn)
    user = _FakeUser()
    posted_titles: list[str] = []
    try:
        await ensure_walmart_exact_verification_queue(db)
        await extreme.ensure_extreme_tables(db)
        if opt_in_guild:
            await extreme.set_extreme_alerts_enabled(db, 295, True)
        await save_dm_deal_alert_preference(
            db, _preference(categories=("brand:samsung",))
        )

        now = datetime.now(timezone.utc).isoformat()
        phone = _samsung_candidate()
        await conn.execute(
            f"INSERT INTO {QUEUE_TABLE} "
            "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
            "status, verified_at, exact_current_cents, snapshot_json) "
            "VALUES (?, ?, ?, ?, 'verified_no_reference', ?, 100, ?)",
            ("29500011", now, now, now, now, _candidate_snapshot(phone)),
        )
        await conn.commit()

        async def fake_live(db_arg, bot_arg, *, required_retailer):
            return LiveGuildLoadResult(guilds=live_guilds)

        async def fake_post(**kwargs):
            for card in kwargs.get("cards", ()):
                posted_titles.append(str(getattr(card.embed, "title", "")))
            return SimpleNamespace(posted=1, errors=())

        monkeypatch.setattr(extreme, "list_live_public_alert_guilds", fake_live)
        monkeypatch.setattr(extreme, "maybe_post_public_deal_cards", fake_post)
        bot = SimpleNamespace(
            db=db, get_user=lambda user_id: user, fetch_user=None
        )
        first = await extreme.fanout_recent_extreme_walmart_prices(bot)
        second = await extreme.fanout_recent_extreme_walmart_prices(bot)
        return first, second, user, posted_titles
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_extreme_dm_fires_without_any_public_lane_opt_in(monkeypatch) -> None:
    # No guild ever opted into the public extreme lane: before the fix the
    # fanout returned early and the DM subscriber got nothing.
    first, second, user, posted_titles = await _run_extreme_fanout(
        monkeypatch=monkeypatch, opt_in_guild=False
    )

    assert first["examined"] == 1
    assert first["eligible"] == 1
    assert first["posted"] == 0
    assert posted_titles == []
    assert first["dm_preferences_checked"] == 1
    assert first["dm_matches"] == 1
    assert first["dm_sent"] == 1
    assert len(user.sent) == 1
    dm_text = str(user.sent[0]["embed"].to_dict()).lower()
    assert "no verified was-price, no discount claimed" in dm_text
    # The cursor advanced past the DM-only lead: no repeat delivery.
    assert second["examined"] == 0
    assert second["dm_sent"] == 0
    assert len(user.sent) == 1


@pytest.mark.asyncio
async def test_extreme_dm_waits_when_opted_in_guild_is_not_live(monkeypatch) -> None:
    # A guild DID opt into the public lane but is absent from Discord's
    # live cache: the cursor is still retained so the public post is not
    # lost — the DM waits with it and fires once the guild is live again.
    first, second, user, posted_titles = await _run_extreme_fanout(
        monkeypatch=monkeypatch, opt_in_guild=True, live_guilds=()
    )

    assert first["examined"] == 1
    assert first["posted"] == 0
    assert first["dm_sent"] == 0
    assert posted_titles == []
    assert user.sent == []
    assert second["examined"] == 1
    assert second["dm_sent"] == 0
