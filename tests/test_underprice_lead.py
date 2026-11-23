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


# ------------------------------------------------------- $99.99 fallback


def _pen_candidate(**overrides) -> SourceCandidate:
    """The exact live shape behind Felix's 2026-11-23 screenshot:
    Pen+Gear pens (item 428327170) at their everyday $2.94, with
    Walmart's API returning a $99.99 'list' the product page never
    shows (no was-price; the 5-pack is $14.70 = 5 x $2.94)."""

    base = dict(
        title=(
            "Pen+Gear Retractable Ballpoint Pens, Medium Point, 1.0 mm, "
            "Black Ink, 8 Count"
        ),
        current_price=2.94,
        api_current_price=2.94,
    )
    base.update(overrides)
    candidate = _bf2042_candidate(**base)
    return _with_attrs(
        candidate,
        referenceContextPrice="99.99",
        categoryPath=(
            "Home Page/Office Supplies/Office Supplies & School "
            "Supplies by Brand/Pen+Gear/Pen+Gear Writing Utensils"
        ),
    )


def test_pen_gear_pens_are_not_an_underprice_lead() -> None:
    assert underprice_lead_reference(_pen_candidate()) is None
    assert build_underprice_lead_card(_pen_candidate()) is None


def test_placeholder_9999_list_rejected_for_any_product() -> None:
    # The sentinel alone must kill it, even for the Battlefield shape
    # that legitimately fires at a real $69.99 / $61.59 list.
    candidate = _with_attrs(
        _bf2042_candidate(), referenceContextPrice="99.99"
    )
    assert underprice_lead_reference(candidate) is None


def test_placeholder_value_skipped_for_a_real_list_behind_it() -> None:
    from sniperplug.services.walmart_extreme_price_signals import (
        walmart_listed_reference,
    )

    candidate = _with_attrs(
        _bf2042_candidate(),
        msrp="99.99",
        referenceContextPrice="",
        listPrice="129.99",
    )
    assert walmart_listed_reference(candidate) == 129.99
    only_placeholder = _with_attrs(
        _bf2042_candidate(),
        msrp="99.99",
        referenceContextPrice="",
        listPrice="",
    )
    assert walmart_listed_reference(only_placeholder) is None


def test_office_supplies_rejected_even_with_a_non_placeholder_list() -> None:
    # Stationery screen on its own: a $59.99 list is not the sentinel,
    # so only the low-ticket-supplies rule can reject this.
    candidate = _with_attrs(
        _pen_candidate(), referenceContextPrice="59.99"
    )
    assert underprice_lead_reference(candidate) is None


def test_office_category_alone_rejected() -> None:
    candidate = _bf2042_candidate(
        title="Desktop Manual Tape Dispenser, 1 in Core, Black",
        current_price=5.99,
        api_current_price=5.99,
    )
    candidate = _with_attrs(
        candidate,
        referenceContextPrice="59.99",
        categoryPath="Home Page/Office Supplies/Desk Accessories",
    )
    assert underprice_lead_reference(candidate) is None


# ------------------------------------------------- round 2: fake-list classes
# Real shapes from the saved 2026-11-23 fire set (live bridge DB replay):
# after the $99.99/stationery fix the lane still fired on 20 saved rows,
# 15 of them gift cards, food/grocery or cleaning chemicals against
# inflated non-placeholder lists. Felix ordered those classes killed.


def _saved_fire_candidate(title, price, listed, category):
    candidate = _bf2042_candidate(
        title=title, current_price=price, api_current_price=price
    )
    return _with_attrs(
        candidate,
        referenceContextPrice=str(listed),
        categoryPath=category,
    )


def test_gift_card_denomination_menu_is_not_an_underprice_lead() -> None:
    # Item 1035672794: a $5 denomination option against the largest
    # denomination returned as the "list".
    candidate = _saved_fire_candidate(
        "Wonderful Mom Walmart Gift Card",
        5.0,
        500.01,
        "Home Page/Gifts & Registry/Gift Cards/Shop Gift Cards by Format/"
        "Plastic Gift Cards/Walmart Plastic Gift Cards/"
        "All Walmart Plastic Gift Cards",
    )
    assert underprice_lead_reference(candidate) is None
    assert build_underprice_lead_card(candidate) is None


def test_egift_card_rejected_on_title_and_category() -> None:
    candidate = _saved_fire_candidate(
        "Basic Blue Walmart eGift Card [Email Delivery]",
        5.0,
        50.0,
        "Home Page/Gifts & Registry/Gift Cards/Shop by Occasion/"
        "Graduation Gift Cards",
    )
    assert underprice_lead_reference(candidate) is None


def test_soup_with_absurd_list_is_not_an_underprice_lead() -> None:
    # Item 10321692: everyday $1.96 soup against a $402.99 list.
    candidate = _saved_fire_candidate(
        "Campbell's Condensed Golden Mushroom Soup, 10.5 oz Can",
        1.96,
        402.99,
        "Home Page/Food/Pantry/Soup/Condensed Soup",
    )
    assert underprice_lead_reference(candidate) is None


def test_seasoning_with_absurd_list_is_not_an_underprice_lead() -> None:
    # Item 379517847: $0.48 seasoning against a $1,149.99 list — the
    # category screen alone kills it, no price ceiling needed.
    candidate = _saved_fire_candidate(
        "Great Value Taco Seasoning Mix, Original, 1 oz",
        0.48,
        1149.99,
        "Home Page/Food/Pantry/Tex Mex",
    )
    assert underprice_lead_reference(candidate) is None


def test_pet_food_is_food_even_under_a_pets_category() -> None:
    # Item 637083809: categoryPath sits under Pets, not Food.
    candidate = _saved_fire_candidate(
        "Freshpet Healthy & Natural Grain Free Small Dogs/Breeds Dog Food, "
        "Fresh Chicken Recipe, 1lb",
        6.87,
        89.88,
        "Home Page/Pets/Pets Subscription",
    )
    assert underprice_lead_reference(candidate) is None


def test_bleach_is_not_an_underprice_lead() -> None:
    # Item 883632494: everyday $6.26 bleach against a $113.99 list.
    candidate = _saved_fire_candidate(
        "Clorox No Splash Bleach, 77 fl oz",
        6.26,
        113.99,
        "Home Page/Household Essentials/Laundry/Bleach/Clorox Bleach",
    )
    assert underprice_lead_reference(candidate) is None
    assert build_underprice_lead_card(candidate) is None


def test_drain_cleaner_is_not_an_underprice_lead() -> None:
    candidate = _saved_fire_candidate(
        "Drano Max Gel Drain Clog Remover, 80 oz, 1 Count",
        9.47,
        108.21,
        "Home Page/Household Essentials/Cleaning Supplies/Drain Cleaners/"
        "Drain Cleaners",
    )
    assert underprice_lead_reference(candidate) is None


def test_food_processor_appliance_is_not_food() -> None:
    # The food category screen is segment-anchored: a kitchen appliance
    # under Kitchen & Dining must not be mistaken for grocery.
    candidate = _saved_fire_candidate(
        "Ninja Professional Food Processor, 1000W, Black",
        19.99,
        199.99,
        "Home Page/Kitchen & Dining/Small Kitchen Appliances/"
        "Food Processors",
    )
    assert underprice_lead_reference(candidate) == 199.99


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


# ------------------------------------------------------- seller name forms


def _named(seller: str):
    return SimpleNamespace(seller_name=seller)


def test_same_seller_name_forms() -> None:
    # Walmart's fetch paths record the first-party seller as both
    # "Walmart" and "Walmart.com"; the live re-check must treat those
    # as the same seller in both directions (observed live on item
    # 599917754, where the mismatch rejected a genuine lead).
    assert extreme._same_seller_name(_named("Walmart"), _named("Walmart.com"))
    assert extreme._same_seller_name(_named("Walmart.com"), _named("Walmart"))
    assert extreme._same_seller_name(_named("Microsoft"), _named("Microsoft"))
    assert extreme._same_seller_name(_named("  Microsoft "), _named("microsoft"))


def test_different_seller_names_still_fail() -> None:
    assert not extreme._same_seller_name(_named("Walmart"), _named("Microsoft"))
    assert not extreme._same_seller_name(_named("Walmart.com"), _named("BargainDeals4U"))
    assert not extreme._same_seller_name(_named(""), _named("Walmart"))
    assert not extreme._same_seller_name(_named("Walmart"), _named(""))


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
