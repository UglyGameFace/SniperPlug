"""First-party underprice leads: the mirror of the listing-error lane.

Battlefield 2042 (Walmart item 599917754) sat at an exact-verified
$2.97 on 2026-11-21 with Walmart's own list price at $69.99, sold by
Walmart.com, and no was-price anywhere. Every existing lane missed it
by design:

* no was-price, and the list price at a 23x ratio is discarded as a
  "suspicious reference" for verified-markdown purposes;
* a fresh database has no stable observed price to drop against;
* the public extreme-price lane excludes game software/digital goods
  and sub-$100 list prices (``insufficient_value_evidence``);
* the listing-error detector needs the listing to contradict itself —
  this listing is internally consistent.

The underprice lane closes exactly that hole, as a DM-only speculative
lead: exact-verified price at or below 20% of Walmart's own list price
(>= $50), sold by Walmart or a platform owner's store, screened against
accessories / consumables / MSRP-inflated departments. Personal
matching ranks it like an evidence-backed listing lead — implied gap
versus the list price on the price-mistake route, all guards unchanged.
It is never posted publicly and never claims a verified discount.
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
from sniperplug.services.dm_deal_routes import dm_route_tokens
from sniperplug.services.underprice_lead import (
    build_underprice_lead_card,
    underprice_lead_reference,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_snapshot,
    ensure_walmart_exact_verification_queue,
)


ITEM_ID = "599917754"


def _bf2042_candidate(**overrides) -> SourceCandidate:
    base = dict(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title="Battlefield 2042 - Xbox Series X|S, Xbox One [Digital]",
        product_url=f"https://www.walmart.com/ip/{ITEM_ID}",
        direct_product_url=f"https://www.walmart.com/ip/{ITEM_ID}",
        current_price=2.97,
        api_current_price=2.97,
        product_id=ITEM_ID,
        product_id_type="sku",
        sku=ITEM_ID,
        selected_offer_id="D4FF50EB09224E378F27E8533147310F",
        seller_name="Walmart.com",
        fulfillment_type="online",
        stock_status="Available",
        can_add_to_cart=True,
        variant_attributes={
            "seller": "Walmart.com",
            "walmartSeller": "yes",
            "exactDetailItemId": ITEM_ID,
            "exactDetailPriceProof": "yes",
            "referenceContextPrice": "69.99",
            "referenceContextSource": "msrp",
            "categoryPath": (
                "Home Page/Video Games/Xbox/Xbox Series X|S/"
                "Xbox Games/Xbox Series X|S Games"
            ),
            "availableOnline": "true",
        },
    )
    base.update(overrides)
    return SourceCandidate(**base)


def _with_attrs(candidate: SourceCandidate, **attrs) -> SourceCandidate:
    merged = dict(candidate.variant_attributes or {})
    merged.update(attrs)
    candidate.variant_attributes = merged
    return candidate


# ---------------------------------------------------------------- detector


def test_bf2042_shape_is_an_underprice_lead() -> None:
    # Digital game software is exactly what the public lane excludes;
    # this DM-only lane exists for it.
    assert underprice_lead_reference(_bf2042_candidate()) == 69.99


def test_platform_owner_seller_qualifies() -> None:
    candidate = _with_attrs(
        _bf2042_candidate(seller_name="Microsoft"),
        seller="Microsoft",
        walmartSeller="no",
    )
    assert underprice_lead_reference(candidate) == 69.99


def test_unknown_marketplace_seller_rejected() -> None:
    candidate = _with_attrs(
        _bf2042_candidate(seller_name="BargainDeals4U"),
        seller="BargainDeals4U",
        walmartSeller="no",
    )
    assert underprice_lead_reference(candidate) is None


def test_requires_exact_detail_proof() -> None:
    candidate = _with_attrs(_bf2042_candidate(), exactDetailPriceProof="no")
    assert underprice_lead_reference(candidate) is None


def test_ratio_gate() -> None:
    # $20 against a $69.99 list is 29% — a deep sale shape, not an
    # error shape. No lead.
    candidate = _bf2042_candidate(current_price=20.00, api_current_price=20.00)
    assert underprice_lead_reference(candidate) is None


def test_list_price_floor() -> None:
    candidate = _with_attrs(
        _bf2042_candidate(), referenceContextPrice="39.99"
    )
    assert underprice_lead_reference(candidate) is None


def test_msrp_inflated_department_rejected() -> None:
    candidate = _bf2042_candidate(
        title="Women's 14K Gold Necklace - 18 inch",
    )
    candidate = _with_attrs(candidate, referenceContextPrice="200.00")
    candidate.current_price = 9.99
    candidate.api_current_price = 9.99
    assert underprice_lead_reference(candidate) is None


def test_accessory_rejected() -> None:
    candidate = _bf2042_candidate(title="Xbox Series X Controller Case")
    assert underprice_lead_reference(candidate) is None


def test_consumable_rejected() -> None:
    candidate = _bf2042_candidate(title="Tide Laundry Detergent 150 oz")
    candidate = _with_attrs(candidate, referenceContextPrice="120.00")
    candidate.current_price = 10.00
    candidate.api_current_price = 10.00
    assert underprice_lead_reference(candidate) is None


def test_not_buyable_rejected() -> None:
    assert (
        underprice_lead_reference(_bf2042_candidate(can_add_to_cart=False))
        is None
    )
    assert (
        underprice_lead_reference(_bf2042_candidate(stock_status="Out of Stock"))
        is None
    )


def test_no_list_reference_rejected() -> None:
    candidate = _with_attrs(_bf2042_candidate(), referenceContextPrice="")
    assert underprice_lead_reference(candidate) is None


# ---------------------------------------------------------------- card


def test_card_is_labelled_speculative_and_claims_no_discount() -> None:
    card = build_underprice_lead_card(_bf2042_candidate())
    assert card is not None
    assert card.underprice_lead is True
    assert card.should_alert is True
    assert card.embed.title.startswith("Underprice lead:")
    text = " ".join(
        [str(card.embed.title), str(card.embed.footer.text or "")]
        + [f"{f.name} {f.value}" for f in card.embed.fields]
    )
    assert "NOT a verified markdown" in text
    assert "$2.97" in text
    assert "$69.99" in text
    assert "unverified" in text
    # The scout card's derived percentage framing must be gone: the only
    # numbers presented are the two prices themselves.
    assert "Scout price" not in text
    assert "% OFF" not in text.upper().replace("NOT A VERIFIED", "")


def test_card_none_when_detector_fails() -> None:
    candidate = _with_attrs(
        _bf2042_candidate(seller_name="BargainDeals4U"),
        seller="BargainDeals4U",
        walmartSeller="no",
    )
    assert build_underprice_lead_card(candidate) is None


# ---------------------------------------------------------------- matching


def _felix_pref(**overrides) -> DmDealAlertPreference:
    categories = tuple(
        list(dm_route_tokens(("price_glitch", "hot_deals")))
        + [
            "tools", "cpus", "fragrance_beauty", "gold_jewelry", "ssds",
            "mobile_accessories", "apple", "brand_direct_electronics",
            "gpus", "watches", "motor_oil", "ram", "walmart_cash",
            "flip_profit:3000",
        ]
    )
    base = dict(
        user_id=629459300854661120,
        enabled=True,
        min_discount=50,
        categories=categories,
    )
    base.update(overrides)
    return DmDealAlertPreference(**base)


def test_felix_shaped_subscriber_matches_bf2042_lead() -> None:
    card = build_underprice_lead_card(_bf2042_candidate())
    assert card is not None
    decision = match_dm_deal(_felix_pref(), card)
    assert decision.matched, decision.reason
    assert "Underprice lead" in decision.reason
    assert "implied gap vs $69.99 Walmart list price" in decision.reason
    assert "not a verified markdown" in decision.reason
    assert "meets your 50% floor" in decision.reason
    assert "route" in decision.reason


def test_underprice_lead_route_off_no_dm() -> None:
    card = build_underprice_lead_card(_bf2042_candidate())
    pref = _felix_pref(
        categories=tuple(dm_route_tokens(("hot_deals",))) + ("gpus",)
    )
    decision = match_dm_deal(pref, card)
    assert not decision.matched


def test_underprice_lead_below_floor_no_dm() -> None:
    # A qualifying lead with a smaller implied gap: $12.59 against the
    # $69.99 list is an 82% gap — still an error shape for the detector
    # (<= 20% of list), but under a 95% subscriber floor it must not DM.
    # (Preference floors are capped at 95 by normalization.)
    candidate = _bf2042_candidate(current_price=12.59, api_current_price=12.59)
    card = build_underprice_lead_card(candidate)
    assert card is not None
    decision = match_dm_deal(_felix_pref(min_discount=95), card)
    assert not decision.matched
    assert "below the required 95%" in decision.reason


def test_underprice_lead_respects_flip_profit_floor() -> None:
    # Route-only match (no category/product layer): the price-mistake
    # profit floor applies to the implied spread, exactly as for
    # referenced deals. $67.02 of implied savings clears a $30 floor
    # but not an $80 floor.
    card = build_underprice_lead_card(_bf2042_candidate())
    categories = tuple(
        list(dm_route_tokens(("price_glitch", "hot_deals")))
        + ["flip_profit:8000"]
    )
    decision = match_dm_deal(_felix_pref(categories=categories), card)
    assert not decision.matched
    assert "price mistake spread" in decision.reason


def test_underprice_lead_respects_max_price_cap() -> None:
    card = build_underprice_lead_card(_bf2042_candidate())
    decision = match_dm_deal(_felix_pref(max_price_cents=200), card)
    assert not decision.matched
    assert "above your maximum" in decision.reason


def test_underprice_card_without_detector_evidence_fails_closed() -> None:
    # A card flagged as an underprice lead whose candidate no longer
    # satisfies the detector (e.g. seller changed) must not become a
    # no-reference interest lead or a referenced deal: it is rejected
    # for incomplete price proof, exactly like an ordinary card.
    candidate = _with_attrs(
        _bf2042_candidate(seller_name="BargainDeals4U"),
        seller="BargainDeals4U",
        walmartSeller="no",
    )
    card = build_underprice_lead_card(_bf2042_candidate())
    assert card is not None
    card.underprice_lead_candidate = candidate
    decision = match_dm_deal(_felix_pref(), card)
    assert not decision.matched
    assert "price proof is incomplete" in decision.reason


# ---------------------------------------------------------------- fanout


@pytest.fixture(autouse=True)
def _fanout_unit_rechecks(monkeypatch):
    """Unit fanout uses static proven snapshots; live-API behavior has
    dedicated coverage in the live recheck functions themselves."""

    async def _verified_extreme(_candidate, *, page_price=False):
        return None

    async def _verified_underprice(_candidate):
        return None

    monkeypatch.setattr(
        extreme, "live_extreme_offer_rejection_reason", _verified_extreme
    )
    monkeypatch.setattr(
        extreme, "live_underprice_rejection_reason", _verified_underprice
    )


class _FakeUser:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=1)


@pytest.mark.asyncio
async def test_fanout_dms_underprice_lead_and_never_posts_it(monkeypatch) -> None:
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
        await save_dm_deal_alert_preference(db, _felix_pref())

        now = datetime.now(timezone.utc).isoformat()
        candidate = _bf2042_candidate()
        await conn.execute(
            f"INSERT INTO {QUEUE_TABLE} "
            "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
            "status, verified_at, exact_current_cents, snapshot_json) "
            "VALUES (?, ?, ?, ?, 'verified_no_reference', ?, 297, ?)",
            (ITEM_ID, now, now, now, now, _candidate_snapshot(candidate)),
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
        bot = SimpleNamespace(db=db, get_user=lambda user_id: user, fetch_user=None)

        first = await extreme.fanout_recent_extreme_walmart_prices(bot)
        second = await extreme.fanout_recent_extreme_walmart_prices(bot)

        # DM'd exactly once, to the opted-in price-mistake subscriber.
        assert first["dm_sent"] == 1, first
        assert len(user.sent) == 1
        # Never posted publicly, on either pass.
        assert posted_titles == []
        assert first["posted"] == 0
        # Cursor advanced: the second pass does not re-examine or re-DM.
        assert second["dm_sent"] == 0
        assert len(user.sent) == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fanout_underprice_lead_live_recheck_failure_holds_cursor(
    monkeypatch,
) -> None:
    from sniperplug.services import dm_deal_alerts as alerts
    from sniperplug.services import dm_delivery_policy as policy

    alerts._SCHEMA_READY = False
    policy._SCHEMA_READY = False

    async def _fetch_failed(_candidate):
        return "live_fetch_failed"

    monkeypatch.setattr(
        extreme, "live_underprice_rejection_reason", _fetch_failed
    )

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = SimpleNamespace(require_conn=lambda: conn)
    user = _FakeUser()
    try:
        await ensure_walmart_exact_verification_queue(db)
        await extreme.ensure_extreme_tables(db)
        await save_dm_deal_alert_preference(db, _felix_pref())

        now = datetime.now(timezone.utc).isoformat()
        candidate = _bf2042_candidate()
        await conn.execute(
            f"INSERT INTO {QUEUE_TABLE} "
            "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
            "status, verified_at, exact_current_cents, snapshot_json) "
            "VALUES (?, ?, ?, ?, 'verified_no_reference', ?, 297, ?)",
            (ITEM_ID, now, now, now, now, _candidate_snapshot(candidate)),
        )
        await conn.commit()
        bot = SimpleNamespace(db=db, get_user=lambda user_id: user, fetch_user=None)

        first = await extreme.fanout_recent_extreme_walmart_prices(bot)
        second = await extreme.fanout_recent_extreme_walmart_prices(bot)

        # A transient live-check failure must not consume the lead: no
        # DM, and the row is re-examined on the next pass.
        assert first["dm_sent"] == 0
        assert user.sent == []
        assert first["examined"] == 1
        assert second["examined"] == 1
    finally:
        await conn.close()
