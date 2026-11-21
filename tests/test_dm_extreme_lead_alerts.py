"""Personal alerts for no-reference and page-price glitches.

Before this fix, ``match_dm_deal`` rejected any card without an exact
current + reference + discount triple, and the extreme lane never ran the DM
path at all (the bulk fanout only ingests verified_markdown rows). A user who
asked for iPhone alerts therefore got no DM for a $1 no-reference iPhone or
a $1 page-price iPhone, even though the extreme lane could post it publicly.

The lead path is deliberately narrow: only cards the extreme/page-price lane
itself built, only for subscribers with an explicit product or brand
interest, with no discount ever claimed. The markdown cutoffs (10/40/20)
are untouched — a normal markdown deal still has to clear them.
"""

from __future__ import annotations

import asyncio
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
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_snapshot,
    ensure_walmart_exact_verification_queue,
)


# ---------------------------------------------------------------- fixtures


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


def _iphone_page_candidate(*, price: float = 1.0) -> SourceCandidate:
    item_id = "425031866"
    return SourceCandidate(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title="Apple iPhone 11 - AT&T - 64GB Green",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=None,
        api_current_price=None,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id="OFFER123",
        seller_name="Walmart",
        fulfillment_type="shipping",
        stock_status="In stock",
        can_add_to_cart=True,
        variant_attributes={
            "sellerId": "F55CDC31AB754BB68FE0B39041159D63",
            "seller": "Walmart",
            "walmartSeller": "yes",
            "exactDetailItemId": item_id,
            "exactDetailPriceProof": "yes",
            "exactDetailOfferId": "OFFER123",
            "pageIdentityRecoveryStatus": "verified_identity",
            "pageIdentityRecoveryPrice": f"{price:.2f}",
            "pageIdentityRecoverySource": "script[0]",
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


# ---------------------------------------------------------------- matcher


def test_no_reference_extreme_lead_matches_explicit_brand_subscriber() -> None:
    card = extreme.build_extreme_price_card(_samsung_candidate())
    assert card is not None

    decision = match_dm_deal(_preference(categories=("brand:samsung",)), card)

    assert decision.matched
    assert "no verified was-price, no discount claimed" in decision.reason
    assert "$1.00" in decision.reason
    assert "99%" not in decision.reason


def test_no_reference_extreme_lead_ignores_category_only_subscriber() -> None:
    card = extreme.build_extreme_price_card(_samsung_candidate())
    assert card is not None

    decision = match_dm_deal(
        _preference(categories=("mobile_accessories",)), card
    )

    assert not decision.matched
    assert "explicit product or brand interest" in decision.reason


def test_no_reference_extreme_lead_ignores_wrong_brand() -> None:
    card = extreme.build_extreme_price_card(_samsung_candidate())
    assert card is not None

    decision = match_dm_deal(_preference(categories=("brand:apple",)), card)

    assert not decision.matched


def test_no_reference_card_without_lane_marker_still_rejected() -> None:
    # The lead path is for extreme-lane cards only. An ordinary card that
    # merely lacks a reference price keeps the old exact-proof rejection,
    # even for a subscriber who named the brand.
    card = SimpleNamespace(
        label="Samsung Galaxy S24 FE",
        retailer="walmart",
        url="https://www.walmart.com/ip/29500011",
        embed=None,
        variant_attributes={"brand": "Samsung"},
        api_current_price=1.0,
        current_price=1.0,
        api_reference_price=None,
        typical_price=None,
        api_discount_percent=None,
        discount=None,
        score=70,
    )

    decision = match_dm_deal(_preference(categories=("brand:samsung",)), card)

    assert not decision.matched
    assert "exact current/was price proof is incomplete" in decision.reason


def test_page_price_lead_matches_explicit_brand_subscriber() -> None:
    card = extreme.build_page_price_extreme_card(_iphone_page_candidate())
    assert card is not None

    decision = match_dm_deal(_preference(categories=("brand:apple",)), card)

    assert decision.matched
    assert "no verified was-price, no discount claimed" in decision.reason
    assert "$1.00" in decision.reason


def test_page_price_lead_ignores_category_only_subscriber() -> None:
    card = extreme.build_page_price_extreme_card(_iphone_page_candidate())
    assert card is not None

    decision = match_dm_deal(
        _preference(categories=("mobile_accessories",)), card
    )

    assert not decision.matched
    assert "explicit product or brand interest" in decision.reason


def test_extreme_lead_respects_hard_max_price() -> None:
    card = extreme.build_extreme_price_card(_samsung_candidate())
    assert card is not None

    decision = match_dm_deal(
        _preference(categories=("brand:samsung",), max_price_cents=50), card
    )

    assert not decision.matched
    assert "above your maximum" in decision.reason


def test_markdown_cutoffs_unchanged_for_ordinary_deals() -> None:
    # A 15%-off markdown in smart mode still fails the 20% floor. The lead
    # path never applies to cards that carry a reference price.
    import discord

    card = SimpleNamespace(
        label="Samsung Galaxy S24 FE",
        retailer="walmart",
        url="https://www.walmart.com/ip/29500011",
        embed=discord.Embed(title="Samsung Galaxy S24 FE"),
        variant_attributes={"brand": "Samsung"},
        api_current_price=85.0,
        current_price=85.0,
        api_reference_price=100.0,
        typical_price=100.0,
        api_discount_percent=15.0,
        discount=15.0,
        score=120,
    )

    decision = match_dm_deal(
        _preference(categories=("brand:samsung",)), card
    )

    assert not decision.matched
    assert "below the required" in decision.reason


# ---------------------------------------------------------------- fanout


@pytest.fixture(autouse=True)
def _fanout_unit_price_recheck(monkeypatch):
    """Unit fanout uses static proven snapshots; live-API behavior has dedicated tests."""

    async def _verified(_candidate, *, page_price=False):
        return None

    monkeypatch.setattr(extreme, "live_extreme_offer_rejection_reason", _verified)


class _FakeUser:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=1)


async def _run_fanout(*, categories, monkeypatch):
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
        await extreme.set_extreme_alerts_enabled(db, 295, True)
        await save_dm_deal_alert_preference(
            db, _preference(categories=categories)
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
            return LiveGuildLoadResult(guilds=(LiveAutoScanGuild(295, 123),))

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
async def test_extreme_fanout_dms_lead_to_brand_subscriber_once(monkeypatch) -> None:
    first, second, user, posted_titles = await _run_fanout(
        categories=("brand:samsung",), monkeypatch=monkeypatch
    )

    assert first["examined"] == 1
    assert first["posted"] == 1
    assert first["dm_preferences_checked"] == 1
    assert first["dm_matches"] == 1
    assert first["dm_sent"] == 1
    assert len(user.sent) == 1
    dm_text = str(user.sent[0]["embed"].to_dict()).lower()
    assert "no verified was-price, no discount claimed" in dm_text
    assert "99%" not in dm_text
    # The public post went out before matching, so no DM badge leaked into it.
    assert posted_titles and "🚨" not in posted_titles[0]
    # The cursor advanced: a second run has nothing left to deliver.
    assert second["examined"] == 0
    assert second["dm_sent"] == 0
    assert len(user.sent) == 1


@pytest.mark.asyncio
async def test_extreme_fanout_skips_dm_for_category_only_subscriber(
    monkeypatch,
) -> None:
    first, _second, user, _posted_titles = await _run_fanout(
        categories=("mobile_accessories",), monkeypatch=monkeypatch
    )

    assert first["examined"] == 1
    assert first["posted"] == 1
    assert first["dm_preferences_checked"] == 1
    assert first["dm_matches"] == 0
    assert first["dm_sent"] == 0
    assert user.sent == []
