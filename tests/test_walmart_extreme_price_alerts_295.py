from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services import walmart_extreme_price_fanout as extreme
from sniperplug.services.autoscan_live_guild_reconciliation import (
    LiveAutoScanGuild,
    LiveGuildLoadResult,
)
from sniperplug.services.public_deal_quality import is_public_deal_candidate
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _candidate_snapshot,
    ensure_walmart_exact_verification_queue,
)


def candidate(*, title="(Open Box) Samsung Galaxy S24 FE Graphite 128 GB DEMO", price=1.0):
    item_id = "29500011"
    return SourceCandidate(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title=title,
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


def test_extreme_exact_price_can_qualify_without_a_markdown_reference() -> None:
    phone = candidate()
    assert extreme.eligible_exact_extreme_candidate(phone)
    card = extreme.build_extreme_price_card(phone)
    assert card is not None
    assert extreme.valid_extreme_price_card(card)
    assert is_public_deal_candidate(card, min_discount=40) is False
    text = str(card.embed.to_dict()).lower()
    assert "not a verified markdown" in text
    assert "no verified reference price or discount" in text
    assert "$1.00" in text
    assert "99%" not in text


def test_extreme_exact_price_still_blocks_accessories_and_unproven_offers() -> None:
    from dataclasses import replace

    base = candidate()
    assert not extreme.eligible_exact_extreme_candidate(replace(base, title="Galaxy S24 FE Case"))
    assert not extreme.eligible_exact_extreme_candidate(replace(base, current_price=99))
    assert not extreme.eligible_exact_extreme_candidate(replace(base, api_current_price=99))
    assert not extreme.eligible_exact_extreme_candidate(replace(base, can_add_to_cart=False))
    assert not extreme.eligible_exact_extreme_candidate(replace(base, stock_status="Sold out"))
    assert not extreme.eligible_exact_extreme_candidate(replace(base, selected_offer_id=None))
    assert not extreme.eligible_exact_extreme_candidate(replace(base, product_url="https://www.walmart.com/ip/999", direct_product_url="https://www.walmart.com/ip/999"))
    attrs = dict(base.variant_attributes)
    attrs["exactDetailPriceProof"] = "no"
    assert not extreme.eligible_exact_extreme_candidate(replace(base, variant_attributes=attrs))


def test_extreme_fanout_optin_cursor_and_retry(monkeypatch) -> None:
    async def scenario():
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = SimpleNamespace(require_conn=lambda: conn)
        try:
            await ensure_walmart_exact_verification_queue(db)
            await extreme.ensure_extreme_tables(db)
            assert await extreme.extreme_alerts_enabled(db, 295) is False
            await extreme.set_extreme_alerts_enabled(db, 295, True)
            assert await extreme.extreme_alerts_enabled(db, 295)

            now = datetime.now(timezone.utc).isoformat()
            phone = candidate()
            await conn.execute(
                f"INSERT INTO {QUEUE_TABLE} "
                "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
                "status, verified_at, exact_current_cents, snapshot_json) "
                "VALUES (?, ?, ?, ?, 'verified_no_reference', ?, 100, ?)",
                ("29500011", now, now, now, now, _candidate_snapshot(phone)),
            )
            await conn.commit()

            async def fake_live(db_arg, bot_arg, *, required_retailer):
                assert required_retailer == "walmart"
                return LiveGuildLoadResult(guilds=(LiveAutoScanGuild(295, 123),))

            calls = []
            async def fake_post(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return SimpleNamespace(posted=0, errors=("Discord temporarily unavailable",))
                return SimpleNamespace(posted=1, errors=())

            monkeypatch.setattr(extreme, "list_live_public_alert_guilds", fake_live)
            monkeypatch.setattr(extreme, "maybe_post_public_deal_cards", fake_post)
            bot = SimpleNamespace(db=db)
            first = await extreme.fanout_recent_extreme_walmart_prices(bot)
            assert first["examined"] == 1
            assert first["posted"] == 0
            second = await extreme.fanout_recent_extreme_walmart_prices(bot)
            assert second["posted"] == 1
            assert calls[-1]["allow_verified_extreme"] is True
            assert calls[-1]["source_label"] == "walmart_extreme_exact_price"
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["examined"] == 0
            await extreme.set_extreme_alerts_enabled(db, 295, False)
            assert not await extreme.extreme_alerts_enabled(db, 295)
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["posted"] == 0
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_automatic_extreme_lane_does_not_change_generic_scout_or_verified_poster() -> None:
    from inspect import signature
    from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards
    params = signature(maybe_post_public_deal_cards).parameters
    assert params["allow_verified_extreme"].default is False
    assert params["allow_review_scout"].default is False


def test_existing_sender_enforces_extreme_proof_and_scout_dedupe(monkeypatch) -> None:
    from sniperplug.services import public_deal_posts as posts
    from sniperplug.services import deal_category_preferences as categories

    async def scenario():
        sent = []
        recorded = []
        alerts = []

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
            alerts.append(kwargs)
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

        card = extreme.build_extreme_price_card(candidate())
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
        assert alerts[0]["alert_key"] == posts.PUBLIC_SCOUT_ALERT_KEY
        assert recorded[0]["allow_review_scout"] is True
        assert "No verified reference price" in str(sent[0]["embed"].to_dict())

        # A forged card label is insufficient to enter this sender path.
        card.extreme_exact_candidate = None
        rejected = await posts.maybe_post_public_deal_cards(**kwargs)
        assert rejected.posted == 0
        assert rejected.skipped_not_alertable == 1
        assert "extreme_exact_price_proof_rejected" in rejected.not_alertable_reasons
        assert len(sent) == 1

    asyncio.run(scenario())


def test_sender_notes_are_not_mistaken_for_failed_posts() -> None:
    successful = SimpleNamespace(
        attempted=1, posted=1, skipped_duplicate=0,
        skipped_not_alertable=0, skipped_disabled=0, skipped_wrong_retailer=0,
        errors=("Alternative configured channel was used",),
        not_alertable_reasons=(),
    )
    assert extreme.delivery_requires_retry(successful) is False
    critical = SimpleNamespace(**{**vars(successful), "errors": ("CRITICAL: receipt was not confirmed",)})
    assert extreme.delivery_requires_retry(critical) is True
    no_send = SimpleNamespace(**{**vars(successful), "posted": 0, "errors": ("Discord forbidden",)})
    assert extreme.delivery_requires_retry(no_send) is True
    category_failure = SimpleNamespace(
        **{**vars(successful), "posted": 0, "skipped_not_alertable": 1,
           "not_alertable_reasons": ("category_error:database offline",)}
    )
    assert extreme.delivery_requires_retry(category_failure) is True
