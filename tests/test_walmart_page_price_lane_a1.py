"""Phase A1: page-price lane.

A verified product-page price must no longer be recorded and ignored. When
the official API returns no numeric price but page recovery confirms
seller/offer/item identity and exposes a page price, the item becomes a
``verified_page_price`` lead: extreme lane / private review only, never a
verified markdown, never a discount-percentage claim.
"""
from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import (
    WalmartAffiliateConfig,
    WalmartProviderError,
)
from sniperplug.providers.walmart_page_recovery import WalmartPageRecoveryProvider
from sniperplug.services.public_deal_quality import is_public_deal_candidate
from sniperplug.services.walmart_exact_price_enrichment import (
    exact_detail_verified_candidates,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    load_recent_verified_queue_candidates,
    verify_single_exact_item,
)
from sniperplug.services import walmart_extreme_price_fanout as extreme


@pytest.fixture(autouse=True)
def _page_fanout_unit_price_recheck(monkeypatch):
    """Page-lane sender mechanics use a fixed page snapshot, not live HTTP."""
    async def _verified(_candidate, *, page_price=False):
        return None
    monkeypatch.setattr(extreme, "live_extreme_offer_rejection_reason", _verified)


IPHONE_ITEM_ID = "425031866"
IPHONE_URL = (
    "https://www.walmart.com/ip/AT-T-Apple-iPhone-11-64GB-Green/425031866"
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class FixturePageProvider(WalmartPageRecoveryProvider):
    """Page-recovery provider with canned API payload and product-page scripts."""

    def __init__(self, payload: dict, scripts: list[str]):
        super().__init__(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )
        self.payload = payload
        self.scripts = scripts
        self.page_calls = 0

    async def _official_detail_payload(self, item_id: str) -> dict:
        assert str(self.payload.get("itemId")) == str(item_id)
        return copy.deepcopy(self.payload)

    async def _official_exact_search_payload(self, item_id: str) -> dict:
        raise WalmartProviderError(
            f"fixture exact search unavailable for {item_id}",
            retry_kind="api_fixture_unavailable",
        )

    async def _load_product_page_scripts(self, item_id: str) -> list[str]:
        self.page_calls += 1
        return list(self.scripts)


def jsonld_product(
    *,
    item_id: str,
    price: float,
    seller: str,
    seller_id: str | None = None,
    offer_id: str | None = None,
) -> str:
    seller_payload: dict[str, str] = {"@type": "Organization", "name": seller}
    if seller_id:
        seller_payload["id"] = seller_id
    offer: dict[str, object] = {
        "@type": "Offer",
        "price": f"{price:.2f}",
        "seller": seller_payload,
    }
    if offer_id:
        offer["id"] = offer_id
    return json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "sku": item_id,
            "url": f"https://www.walmart.com/ip/{item_id}",
            "offers": offer,
        }
    )


def iphone_api_payload_no_price() -> dict:
    """The $1 iPhone 11 as the official API returns it: item, no price, no seller."""
    return {
        "itemId": int(IPHONE_ITEM_ID),
        "name": "Apple iPhone 11 - AT&T - 64GB Green",
        "availableOnline": True,
    }


def iphone_page_scripts(*, price: float = 1.00) -> list[str]:
    return [
        jsonld_product(
            item_id=IPHONE_ITEM_ID,
            price=price,
            seller="Walmart",
            seller_id="F55CDC31AB754BB68FE0B39041159D63",
            offer_id="OFFER123",
        )
    ]


async def queue_row(conn, item_id: str):
    cursor = await conn.execute(
        f"SELECT status, exact_current_cents, exact_reference_cents, "
        f"exact_discount_bps FROM {QUEUE_TABLE} WHERE item_id = ?",
        (item_id,),
    )
    return await cursor.fetchone()


def page_price_candidate(*, price: float = 1.00, title: str = "Apple iPhone 11 - AT&T - 64GB Green") -> SourceCandidate:
    """A candidate exactly as the page-price lane stores it: no API price."""
    return SourceCandidate(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title=title,
        product_url=f"https://www.walmart.com/ip/{IPHONE_ITEM_ID}",
        direct_product_url=f"https://www.walmart.com/ip/{IPHONE_ITEM_ID}",
        current_price=None,
        api_current_price=None,
        product_id=IPHONE_ITEM_ID,
        product_id_type="sku",
        sku=IPHONE_ITEM_ID,
        selected_offer_id="OFFER123",
        seller_name="Walmart",
        fulfillment_type="shipping",
        stock_status="In stock",
        can_add_to_cart=True,
        variant_attributes={
            "sellerId": "F55CDC31AB754BB68FE0B39041159D63",
            "seller": "Walmart",
            "walmartSeller": "yes",
            "exactDetailItemId": IPHONE_ITEM_ID,
            "exactDetailPriceProof": "yes",
            "exactDetailOfferId": "OFFER123",
            "pageIdentityRecoveryStatus": "verified_identity",
            "pageIdentityRecoveryPrice": f"{price:.2f}",
            "pageIdentityRecoverySource": "script[0]",
        },
    )


# --- Case 1: API no-price + page $1 with confirmed identity -> page-price lead ---


@pytest.mark.asyncio
async def test_page_price_lane_iphone_api_no_price_becomes_verified_page_price() -> None:
    from sniperplug.services.walmart_exact_verification_queue import page_observed_price

    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    provider = FixturePageProvider(iphone_api_payload_no_price(), iphone_page_scripts())

    result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)

    assert provider.page_calls == 1
    assert result.status == "verified"
    assert result.classification == "verified_page_price"
    candidate = result.candidate
    assert candidate is not None
    # The page price never becomes an API price.
    assert candidate.api_current_price is None
    assert candidate.current_price is None
    assert page_observed_price(candidate) == 1.00
    assert candidate.seller_name == "Walmart"

    row = await queue_row(conn, IPHONE_ITEM_ID)
    assert row is not None
    assert row[0] == "verified_page_price"
    assert row[1] is None  # exact_current_cents stays NULL: no API price
    assert row[3] == 0  # exact_discount_bps: no discount is ever computed
    await conn.close()


@pytest.mark.asyncio
async def test_page_price_lane_candidate_is_extreme_eligible_and_card_is_labelled() -> None:
    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    provider = FixturePageProvider(iphone_api_payload_no_price(), iphone_page_scripts())

    result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)
    candidate = result.candidate
    assert candidate is not None

    assert extreme.eligible_page_price_extreme_candidate(candidate)
    card = extreme.build_page_price_extreme_card(candidate)
    assert card is not None
    assert extreme.valid_page_price_extreme_card(card)
    text = str(card.embed.to_dict()).lower()
    assert "page-observed price, not api-verified" in text
    assert "$1.00" in text
    # No discount percentage is ever claimed for a page-observed price.
    assert "99%" not in text
    assert "% off" not in text
    # The page-price card is not a public markdown candidate.
    assert is_public_deal_candidate(card, min_discount=40) is False
    # The ordinary exact-price lane must not accept a price-less candidate.
    assert extreme.eligible_exact_extreme_candidate(candidate) is False
    assert extreme.build_extreme_price_card(candidate) is None
    await conn.close()


# --- Case 2: API price present + page mismatch -> still page_price_mismatch ---


@pytest.mark.asyncio
async def test_page_price_lane_api_price_page_mismatch_stays_mismatch_no_lead() -> None:
    from sniperplug.services.walmart_exact_verification_queue import page_observed_price

    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    payload = {
        "itemId": int(IPHONE_ITEM_ID),
        "name": "Apple iPhone 11 - AT&T - 64GB Green",
        "salePrice": 1.00,
        "availableOnline": True,
    }
    # Page says $2.00 while the API says $1.00: cent-for-cent mismatch.
    provider = FixturePageProvider(payload, iphone_page_scripts(price=2.00))

    result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)

    assert result.candidate is None
    assert result.status == "retry"
    assert "price mismatch" in result.error
    row = await queue_row(conn, IPHONE_ITEM_ID)
    assert row is not None
    assert row[0] == "retry"
    # A mismatched page price is not observable evidence for any lane.
    assert page_observed_price(page_price_candidate()) == 1.00  # control: helper works
    mismatched = page_price_candidate(price=2.00)
    mismatched.variant_attributes["pageIdentityRecoveryStatus"] = "price_mismatch"
    assert page_observed_price(mismatched) is None
    assert extreme.eligible_page_price_extreme_candidate(mismatched) is False
    await conn.close()


# --- Case 3: no confirmed identity -> no lead ---


@pytest.mark.asyncio
async def test_page_price_lane_no_identity_no_lead() -> None:
    from sniperplug.services.walmart_exact_verification_queue import page_observed_price

    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    # No page structured data at all: nothing confirms seller/offer identity.
    provider = FixturePageProvider(iphone_api_payload_no_price(), [])

    result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)

    assert result.candidate is None
    assert result.status in {"incomplete_identity", "retry"}
    row = await queue_row(conn, IPHONE_ITEM_ID)
    assert row is not None
    assert row[0] in {"incomplete_identity", "retry"}

    # Helper-level guards: no status, or a non-verified status, means no price.
    assert page_observed_price(page_price_candidate()) == 1.00  # control
    bare = page_price_candidate()
    bare.variant_attributes.pop("pageIdentityRecoveryStatus", None)
    bare.variant_attributes.pop("pageIdentityRecoveryPrice", None)
    assert page_observed_price(bare) is None
    assert extreme.eligible_page_price_extreme_candidate(bare) is False
    conflicted = page_price_candidate()
    conflicted.variant_attributes["pageIdentityRecoveryStatus"] = "seller_conflict"
    assert page_observed_price(conflicted) is None
    assert extreme.eligible_page_price_extreme_candidate(conflicted) is False
    await conn.close()


@pytest.mark.asyncio
async def test_page_price_lane_not_buyable_page_price_is_not_a_lead() -> None:
    from dataclasses import replace

    candidate = page_price_candidate()
    assert extreme.eligible_page_price_extreme_candidate(candidate)
    assert extreme.eligible_page_price_extreme_candidate(
        replace(candidate, can_add_to_cart=False)
    ) is False
    assert extreme.eligible_page_price_extreme_candidate(
        replace(candidate, stock_status="Sold out")
    ) is False
    over_limit = page_price_candidate(price=10.01)
    assert extreme.eligible_page_price_extreme_candidate(over_limit) is False
    accessory = page_price_candidate(title="Apple iPhone 11 Case")
    assert extreme.eligible_page_price_extreme_candidate(accessory) is False


# --- Case 4: page price never becomes a verified markdown ---


@pytest.mark.asyncio
async def test_page_price_lane_never_reaches_public_markdown_pool() -> None:
    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    provider = FixturePageProvider(iphone_api_payload_no_price(), iphone_page_scripts())

    result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)
    candidate = result.candidate
    assert candidate is not None
    assert result.classification != "verified_markdown"

    # No numeric API price, so the exact-verified pool (public markdown path)
    # must not accept it.
    assert exact_detail_verified_candidates([candidate]) == []
    # The recent-verified loader that feeds public posting must not return it.
    assert await load_recent_verified_queue_candidates(db) == []
    await conn.close()


# --- End-to-end: production off-loop fetch + bulk persistence + fanout ---


@pytest.mark.asyncio
async def test_page_price_lane_off_loop_fetch_returns_page_price_candidate() -> None:
    from sniperplug.services.walmart_exact_queue_runtime import (
        _fetch_exact_candidate_off_loop,
    )
    from sniperplug.services.walmart_exact_verification_queue import _QueueClaim

    provider = FixturePageProvider(iphone_api_payload_no_price(), iphone_page_scripts())
    claim = _QueueClaim(
        item_id=IPHONE_ITEM_ID,
        title="",
        product_url=IPHONE_URL,
        image_url="",
        apparent_current_cents=None,
        apparent_reference_cents=None,
        route_hint="manual_link",
        lease_token="",
        attempt_count=0,
    )

    candidate, status, error = await _fetch_exact_candidate_off_loop(
        provider, claim, timeout_seconds=8.0
    )

    assert candidate is not None
    assert status == "verified"
    assert error == ""
    assert candidate.api_current_price is None
    assert candidate.variant_attributes.get("pageIdentityRecoveryPrice") == "1.00"


@pytest.mark.asyncio
async def test_page_price_lane_bulk_persistence_stores_page_price_status() -> None:
    from sniperplug.services.walmart_exact_queue_bulk_persistence import (
        ExactQueuePersistenceOutcome,
        persist_exact_queue_outcomes_bulk,
    )
    from sniperplug.services.walmart_exact_verification_queue import (
        _classify_exact_candidate,
        ensure_walmart_exact_verification_queue,
    )
    from sniperplug.services.walmart_global_offer_memory import (
        ensure_global_offer_memory_table,
    )

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = FakeDatabase(conn)
    await ensure_walmart_exact_verification_queue(db)
    await ensure_global_offer_memory_table(db)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    await conn.execute(
        f"INSERT INTO {QUEUE_TABLE} "
        "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
        "status, lease_token, attempt_count) VALUES (?, ?, ?, ?, 'pending', 'tok', 0)",
        (IPHONE_ITEM_ID, now_iso, now_iso, now_iso),
    )
    await conn.commit()

    candidate = page_price_candidate()
    assert _classify_exact_candidate(candidate, min_discount=10) == "verified_page_price"
    claim = SimpleNamespace(item_id=IPHONE_ITEM_ID, lease_token="tok", attempt_count=0)
    persisted = await persist_exact_queue_outcomes_bulk(
        conn,
        [
            ExactQueuePersistenceOutcome(
                claim=claim,
                candidate=candidate,
                status="verified",
                error="",
                observed_at=now,
            )
        ],
        min_discount=10,
    )
    assert persisted.queue_rows == 1
    row = await queue_row(conn, IPHONE_ITEM_ID)
    assert row is not None
    assert row[0] == "verified_page_price"
    assert row[1] is None
    await conn.close()


def test_page_price_lane_fanout_posts_page_price_to_opted_in_guild(monkeypatch) -> None:
    from sniperplug.services.autoscan_live_guild_reconciliation import (
        LiveAutoScanGuild,
        LiveGuildLoadResult,
    )

    async def scenario():
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        try:
            provider = FixturePageProvider(
                iphone_api_payload_no_price(), iphone_page_scripts()
            )
            result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)
            assert result.classification == "verified_page_price"

            await extreme.ensure_extreme_tables(db)
            assert await extreme.extreme_alerts_enabled(db, 295) is False
            await extreme.set_extreme_alerts_enabled(db, 295, True)

            async def fake_live(db_arg, bot_arg, *, required_retailer):
                assert required_retailer == "walmart"
                return LiveGuildLoadResult(guilds=(LiveAutoScanGuild(295, 123),))

            calls = []

            async def fake_post(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(posted=1, errors=())

            monkeypatch.setattr(extreme, "list_live_public_alert_guilds", fake_live)
            monkeypatch.setattr(extreme, "maybe_post_public_deal_cards", fake_post)
            bot = SimpleNamespace(db=db)

            first = await extreme.fanout_recent_extreme_walmart_prices(bot)
            assert first["examined"] == 1
            assert first["eligible"] == 1
            assert first["posted"] == 1
            assert calls[-1]["allow_verified_extreme"] is True
            assert calls[-1]["source_label"] == "walmart_extreme_exact_price"
            card = calls[-1]["cards"][0]
            text = str(card.embed.to_dict()).lower()
            assert "page-observed price, not api-verified" in text
            assert "99%" not in text
            # Cursor advanced past the page-price row; a second pass is quiet.
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["examined"] == 0
            # Opting out stops the lane.
            await extreme.set_extreme_alerts_enabled(db, 295, False)
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["posted"] == 0
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_page_price_lane_sender_accepts_page_price_card(monkeypatch) -> None:
    from sniperplug.services import deal_category_preferences as categories
    from sniperplug.services import public_deal_posts as posts

    async def scenario():
        sent = []
        recorded = []

        class Channel:
            id = 901

            async def send(self, **kwargs):
                sent.append(kwargs)
                return SimpleNamespace(id=902)

        async def config(*args, **kwargs):
            return {"enabled": True, "channel_id": 901, "retailers": ("walmart",)}

        async def prefs(*args, **kwargs):
            return {}

        async def channel(*args, **kwargs):
            return Channel(), None

        async def no_recent(*args, **kwargs):
            return None

        async def allowed(*args, **kwargs):
            return True

        async def noop(*args, **kwargs):
            return None

        async def finalize(*args, **kwargs):
            recorded.append(kwargs)
            return True, []

        monkeypatch.setattr(posts, "get_public_alert_config", config)
        monkeypatch.setattr(categories, "get_category_preferences", prefs)
        monkeypatch.setattr(categories, "decide_category", lambda *args: SimpleNamespace(action="allow"))
        monkeypatch.setattr(posts, "resolve_public_alert_channel", channel)
        monkeypatch.setattr(posts, "safe_find_recent_alert", no_recent)
        monkeypatch.setattr(posts, "reserve_public_deal_post", allowed)
        monkeypatch.setattr(posts, "mark_public_deal_sending", noop)
        monkeypatch.setattr(posts, "build_deal_feedback_view", noop)
        monkeypatch.setattr(posts, "finalize_successful_public_post", finalize)

        card = extreme.build_page_price_extreme_card(page_price_candidate())
        assert card is not None
        bot = SimpleNamespace(db=object())
        kwargs = {
            "bot": bot, "guild_id": 295, "cards": [card],
            "source_label": "walmart_extreme_exact_price",
            "fallback_retailer": "walmart",
            "min_public_discount": 40,
            "allow_verified_extreme": True,
        }
        result = await posts.maybe_post_public_deal_cards(**kwargs)
        assert result.posted == 1
        assert len(sent) == 1
        assert "page-observed price, not API-verified" in str(sent[0]["embed"].to_dict())

        # A forged page-price label is insufficient to enter this sender path.
        card.page_price_candidate = None
        rejected = await posts.maybe_post_public_deal_cards(**kwargs)
        assert rejected.posted == 0
        assert rejected.skipped_not_alertable == 1
        assert "page_price_proof_rejected" in rejected.not_alertable_reasons
        assert len(sent) == 1

    asyncio.run(scenario())


def test_page_price_lane_check_deal_embed_labels_page_price() -> None:
    from sniperplug.cogs.deal_scanner import build_page_price_direct_check_embed

    candidate = page_price_candidate()
    embed = build_page_price_direct_check_embed(
        candidate, item_id=IPHONE_ITEM_ID, page_price=1.00, enqueued=True
    )
    text = str(embed.to_dict()).lower()
    assert "page-observed price, not api-verified" in text
    assert "$1.00" in text
    assert "99%" not in text
    assert "% off" not in text
    assert "not a verified markdown" in text
    assert "425031866" in text
