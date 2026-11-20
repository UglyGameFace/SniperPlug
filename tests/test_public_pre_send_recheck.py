"""Live pre-send recheck for public markdown posts (trust fix 2, 2026-11-18).

Ordinary public markdown posts were gated at send time only by a DB
superseded check — the stored queue row, not Walmart now — so a price
verified up to an hour earlier could post after a revert. The bulk fanout
now re-fetches each claimed event's exact item immediately before
delivery through the same trusted exact path the extreme lane uses, and
fails closed: identity/seller changes, lost buyability, price moves
beyond max($0.25, 1%), and recheck errors all skip the post (counted in
``public_skipped_live_recheck``); events past the per-cycle recheck
budget are deferred, never posted unverified.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import discord
import pytest

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services import walmart_global_deal_fanout_bulk as bulk
from sniperplug.services.autoscan_live_guild_reconciliation import (
    LiveAutoScanGuild,
    LiveGuildLoadResult,
)
from sniperplug.services.deal_feedback import ensure_deal_feedback_tables


ITEM_ID = "29500011"


def _candidate(*, price: float = 55.0, seller: str = "Walmart", offer: str = "offer-1") -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title="Samsung Galaxy S24 FE",
        product_url=f"https://www.walmart.com/ip/{ITEM_ID}",
        direct_product_url=f"https://www.walmart.com/ip/{ITEM_ID}",
        current_price=price,
        api_current_price=price,
        product_id=ITEM_ID,
        product_id_type="sku",
        sku=ITEM_ID,
        selected_offer_id=offer,
        seller_name=seller,
        condition="New",
        fulfillment_type="shipping",
        stock_status="In stock",
        can_add_to_cart=True,
        variant_label="Graphite 128 GB",
        variant_attributes={
            "sellerId": "walmart",
            "seller": seller,
            "walmartSeller": "yes",
            "exactDetailItemId": ITEM_ID,
            "exactDetailPriceProof": "yes",
        },
    )


def _patch_fetch(monkeypatch, fetch) -> None:
    monkeypatch.setattr(
        bulk, "provider_registry", SimpleNamespace(get=lambda key: object())
    )
    monkeypatch.setattr(bulk, "_fetch_exact_candidate_off_loop", fetch)


@pytest.mark.asyncio
async def test_price_holds_proceeds(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        return _candidate(price=55.0), "verified", ""

    _patch_fetch(monkeypatch, fetch)
    assert await bulk._live_pre_send_recheck_reason(_candidate(price=55.0)) is None


@pytest.mark.asyncio
async def test_price_within_tolerance_proceeds(monkeypatch) -> None:
    # $55.00 -> $55.40 is inside max($0.25, 1% of $55 = $0.55).
    async def fetch(provider, claim, *, timeout_seconds):
        return _candidate(price=55.40), "verified", ""

    _patch_fetch(monkeypatch, fetch)
    assert await bulk._live_pre_send_recheck_reason(_candidate(price=55.0)) is None


@pytest.mark.asyncio
async def test_price_moved_beyond_tolerance_blocks(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        return _candidate(price=75.0), "verified", ""

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_candidate(price=55.0))
    assert reason == "live_price_changed"


@pytest.mark.asyncio
async def test_identity_changed_blocks(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        return _candidate(price=55.0, offer="offer-2"), "verified", ""

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_candidate(price=55.0))
    assert reason == "live_offer_or_variant_changed"


@pytest.mark.asyncio
async def test_seller_changed_blocks(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        refreshed = _candidate(price=55.0)
        refreshed.seller_name = "Some Other Seller"
        refreshed.variant_attributes = {
            **refreshed.variant_attributes,
            "seller": "Some Other Seller",
            "sellerId": "other-seller",
            "walmartSeller": "no",
        }
        return refreshed, "verified", ""

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_candidate(price=55.0))
    assert reason in {"live_offer_or_variant_changed", "live_seller_name_changed"}


@pytest.mark.asyncio
async def test_recheck_error_fails_closed(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        raise RuntimeError("network down")

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_candidate(price=55.0))
    assert reason == "live_fetch_failed"


@pytest.mark.asyncio
async def test_unverified_fetch_fails_closed(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        return None, "retry", "try again later"

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_candidate(price=55.0))
    assert reason == "live_fetch_unverified"


@pytest.mark.asyncio
async def test_missing_identity_fails_closed(monkeypatch) -> None:
    # No concrete identity fields at all (no item id, no offer id, no
    # seller): there is nothing to expect the live offer to match, so
    # the recheck fails closed before any fetch.
    _patch_fetch(monkeypatch, None)
    candidate = _candidate(price=55.0)
    candidate.variant_attributes = {}
    candidate.selected_offer_id = None
    candidate.product_id = None
    candidate.sku = None
    candidate.product_url = ""
    candidate.direct_product_url = ""
    candidate.seller_name = None
    reason = await bulk._live_pre_send_recheck_reason(candidate)
    assert reason == "original_identity_missing"


@pytest.mark.asyncio
async def test_stored_candidate_without_any_attrs_still_rechecks_live(monkeypatch) -> None:
    # Even with variant_attributes fully absent, a stored candidate with
    # concrete fields (item, offer, seller name) derives an expectation;
    # the live side must still prove itself against it.
    async def fetch(provider, claim, *, timeout_seconds):
        return _candidate(price=55.0), "verified", ""

    _patch_fetch(monkeypatch, fetch)
    candidate = _candidate(price=55.0)
    candidate.variant_attributes = {}
    assert await bulk._live_pre_send_recheck_reason(candidate) is None


# ---------------------------------------------------------------------------
# Stored-snapshot candidates (the production markdown lane).
#
# Claimed fanout events rebuild their candidate from the queue row's
# snapshot_json via _candidate_from_snapshot. Live queue snapshots carry
# the concrete identity fields (item id, selected offer id, seller) but
# NOT the live-enrichment proof stamp (variant_attributes
# exactDetailPriceProof / exactDetailItemId are absent) — that stamp is
# only applied to freshly enriched candidates. The recheck must derive
# the stored side's expected identity from those concrete fields and
# still require the LIVE refreshed candidate to fully prove itself.
# ---------------------------------------------------------------------------

import json


def _stored_snapshot_candidate(*, price: float = 55.0):
    """Build a candidate the way the bulk fanout does: from a queue-style
    snapshot payload with concrete identity fields and no proof stamp."""
    payload = {
        "source_key": "walmart_exact_detail_queue",
        "retailer": "Walmart",
        "title": "Samsung Galaxy S24 FE",
        "product_url": f"https://www.walmart.com/ip/{ITEM_ID}",
        "direct_product_url": f"https://www.walmart.com/ip/{ITEM_ID}",
        "image_url": None,
        "current_price": price,
        "typical_price": 110.0,
        "api_current_price": price,
        "api_reference_price": 110.0,
        "api_discount_percent": 50.0,
        "product_id": ITEM_ID,
        "product_id_type": "sku",
        "sku": ITEM_ID,
        "selected_offer_id": "offer-1",
        "variant_label": "Graphite 128 GB",
        "variant_attributes": {
            "sellerId": "walmart",
            "seller": "Walmart",
            "walmartSeller": "yes",
        },
        "seller_name": "Walmart",
        "fulfillment_type": "shipping",
        "condition": "New",
        "stock_status": "In stock",
        "can_add_to_cart": True,
    }
    candidate = bulk._candidate_from_snapshot(json.dumps(payload))
    assert candidate is not None
    return candidate


@pytest.mark.asyncio
async def test_stored_snapshot_candidate_passes_when_live_offer_proves_itself(monkeypatch) -> None:
    """REPRODUCTION: a stored-snapshot candidate (no proof stamp) whose
    live re-fetch returns the same item/offer/seller at the same price
    WITH the proof stamp must pass the recheck. On the shipped code this
    returns "original_identity_missing" — the outage."""
    async def fetch(provider, claim, *, timeout_seconds):
        return _candidate(price=55.0), "verified", ""

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_stored_snapshot_candidate())
    assert reason is None


@pytest.mark.asyncio
async def test_stored_snapshot_candidate_price_moved_blocks(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        return _candidate(price=75.0), "verified", ""

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_stored_snapshot_candidate())
    assert reason == "live_price_changed"


@pytest.mark.asyncio
async def test_stored_snapshot_candidate_offer_changed_blocks(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        return _candidate(price=55.0, offer="offer-2"), "verified", ""

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_stored_snapshot_candidate())
    assert reason == "live_offer_or_variant_changed"


@pytest.mark.asyncio
async def test_stored_snapshot_candidate_seller_changed_blocks(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        refreshed = _candidate(price=55.0)
        refreshed.seller_name = "Some Other Seller"
        refreshed.variant_attributes = {
            **refreshed.variant_attributes,
            "seller": "Some Other Seller",
            "sellerId": "other-seller",
            "walmartSeller": "no",
        }
        return refreshed, "verified", ""

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_stored_snapshot_candidate())
    assert reason in {"live_offer_or_variant_changed", "live_seller_name_changed"}


@pytest.mark.asyncio
async def test_stored_snapshot_candidate_fetch_failure_blocks(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        raise RuntimeError("network down")

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_stored_snapshot_candidate())
    assert reason == "live_fetch_failed"


@pytest.mark.asyncio
async def test_stored_snapshot_candidate_live_without_proof_blocks(monkeypatch) -> None:
    """The live refreshed candidate must fully prove itself. A live
    candidate that is field-identical but carries no proof stamp is
    rejected: the stored side's stamp-free derivation never lowers the
    live side's bar."""
    async def fetch(provider, claim, *, timeout_seconds):
        refreshed = _candidate(price=55.0)
        refreshed.variant_attributes = {
            key: value
            for key, value in refreshed.variant_attributes.items()
            if key not in {"exactDetailPriceProof", "exactDetailItemId"}
        }
        return refreshed, "verified", ""

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_stored_snapshot_candidate())
    assert reason == "live_offer_or_variant_changed"


@pytest.mark.asyncio
async def test_stored_snapshot_candidate_live_not_buyable_blocks(monkeypatch) -> None:
    async def fetch(provider, claim, *, timeout_seconds):
        refreshed = _candidate(price=55.0)
        refreshed.can_add_to_cart = False
        return refreshed, "verified", ""

    _patch_fetch(monkeypatch, fetch)
    reason = await bulk._live_pre_send_recheck_reason(_stored_snapshot_candidate())
    assert reason == "live_not_buyable"


@pytest.mark.asyncio
async def test_recheck_budget_marks_overflow_events(monkeypatch) -> None:
    async def ok_reason(_candidate):
        return None

    monkeypatch.setattr(bulk, "_live_pre_send_recheck_reason", ok_reason)
    monkeypatch.setattr(bulk, "LIVE_PRE_SEND_RECHECK_LIMIT", 2)
    events = [
        bulk._ClaimedEvent(deal_key=f"key-{index}", claim_token="tok", card=None, candidate=object())
        for index in range(4)
    ]
    reasons = await bulk._live_pre_send_recheck_reasons(events)
    assert reasons == {
        "key-2": bulk.LIVE_RECHECK_BUDGET_REASON,
        "key-3": bulk.LIVE_RECHECK_BUDGET_REASON,
    }


# ---------------------------------------------------------------------------
# Full fanout loop: a failed recheck means no post, counted, event finalized.
# ---------------------------------------------------------------------------


async def _run_fanout(monkeypatch, *, recheck_reasons: dict[str, str]):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = SimpleNamespace(require_conn=lambda: conn)
    await ensure_deal_feedback_tables(db)
    card = SimpleNamespace(
        retailer="walmart",
        label="Samsung Galaxy S24 FE",
        title="Samsung Galaxy S24 FE",
        url=f"https://www.walmart.com/ip/{ITEM_ID}",
        direct_product_url=f"https://www.walmart.com/ip/{ITEM_ID}",
        selected_offer_id="offer-1",
        sku=ITEM_ID,
        score=80,
        discount=45.0,
        current_price=55.0,
        api_current_price=55.0,
        embed=discord.Embed(title="Samsung Galaxy S24 FE"),
    )
    posted_cards: list = []
    finalized: list = []
    deferred: list = []

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
                candidate=_candidate(),
                source_verified_at=datetime.now(timezone.utc).isoformat(),
            )
        ]

    async def _fake_superseded(_db, _events):
        return {}

    async def _fake_rechecks(_events):
        return dict(recheck_reasons)

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

    async def _fake_defer(_db, rows):
        deferred.extend(rows)

    async def _noop() -> None:
        return None

    async def _as_40() -> int:
        return 40

    monkeypatch.setattr(bulk, "ensure_global_deal_event_tables_once", lambda _db: _noop())
    monkeypatch.setattr(bulk, "_ingest_verified_queue_events_bulk", _fake_ingest)
    monkeypatch.setattr(bulk, "_claim_pending_events_bulk", _fake_claim)
    monkeypatch.setattr(bulk, "_superseded_event_keys", _fake_superseded)
    monkeypatch.setattr(bulk, "_live_pre_send_recheck_reasons", _fake_rechecks)
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
    try:
        result = await bulk.fanout_recent_exact_walmart_deals(bot)
    finally:
        await conn.close()
    return result, posted_cards, finalized, deferred


@pytest.mark.asyncio
async def test_fanout_skips_post_when_live_recheck_fails(monkeypatch) -> None:
    result, posted, finalized, deferred = await _run_fanout(
        monkeypatch, recheck_reasons={"walmart:offer-1": "live_price_changed"}
    )
    assert posted == []
    assert result.public_posts == 0
    assert result.public_skipped_live_recheck == 1
    # The event is finalized (not retried forever): the exact queue's own
    # re-probes can create a fresh event at the new price.
    assert finalized == [("walmart:offer-1", "tok-1")]
    assert deferred == []


@pytest.mark.asyncio
async def test_fanout_posts_when_live_recheck_passes(monkeypatch) -> None:
    result, posted, finalized, deferred = await _run_fanout(
        monkeypatch, recheck_reasons={}
    )
    assert len(posted) == 1
    assert result.public_posts == 1
    assert result.public_skipped_live_recheck == 0


@pytest.mark.asyncio
async def test_fanout_defers_when_recheck_budget_exhausted(monkeypatch) -> None:
    result, posted, finalized, deferred = await _run_fanout(
        monkeypatch,
        recheck_reasons={"walmart:offer-1": bulk.LIVE_RECHECK_BUDGET_REASON},
    )
    assert posted == []
    assert result.public_posts == 0
    assert result.public_skipped_live_recheck == 0
    assert finalized == []
    assert len(deferred) == 1
    assert deferred[0][0] == "walmart:offer-1"
