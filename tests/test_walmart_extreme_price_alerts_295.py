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
    assert "no comparison price or discount percentage is claimed" in text
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
    assert not extreme.eligible_exact_extreme_candidate(replace(base, stock_status="Not available online"))
    # Guard against bool("false") == True in upstream Walmart availability.
    for value in ("false", "no", "0", "off"):
        attrs = dict(base.variant_attributes)
        attrs["availableOnline"] = value
        assert not extreme.eligible_exact_extreme_candidate(replace(base, variant_attributes=attrs))
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
        assert "No comparison price or discount percentage is claimed" in str(sent[0]["embed"].to_dict())

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


def test_extreme_optin_and_fanout_accept_turso_mapping_rows(monkeypatch) -> None:
    """Regress production KeyError: 0 in the Turso dictionary-backed cursor.

    Mock only the row representation on a real aiosqlite database so the
    command's COUNT/UPSERT and the worker's opt-in, cursor and queue query all
    exercise their actual SQL without numeric-key shortcuts.
    """
    async def scenario():
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = SimpleNamespace(require_conn=lambda: conn)
        try:
            await ensure_walmart_exact_verification_queue(db)
            await extreme.ensure_extreme_tables(db)
            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                f"INSERT INTO {QUEUE_TABLE} "
                "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
                "status, verified_at, exact_current_cents, snapshot_json) "
                "VALUES (?, ?, ?, ?, 'verified_no_reference', ?, 100, ?)",
                ("29500011", now, now, now, now, _candidate_snapshot(candidate())),
            )
            await conn.commit()
            # This representation reproduces the production Turso failure:
            # dict rows accept named columns but row[0] raises KeyError.
            conn.row_factory = lambda cursor, values: {
                desc[0]: value for desc, value in zip(cursor.description, values)
            }
            assert not await extreme.extreme_alerts_enabled(db, 295)
            await extreme.set_extreme_alerts_enabled(db, 295, True)
            assert await extreme.extreme_alerts_enabled(db, 295)

            async def fake_live(_db, _bot, *, required_retailer):
                assert required_retailer == "walmart"
                return LiveGuildLoadResult(guilds=(LiveAutoScanGuild(295, 123),))

            delivered = []
            async def fake_post(**kwargs):
                delivered.append(kwargs)
                return SimpleNamespace(posted=1, attempted=1, errors=())

            monkeypatch.setattr(extreme, "list_live_public_alert_guilds", fake_live)
            monkeypatch.setattr(extreme, "maybe_post_public_deal_cards", fake_post)
            bot = SimpleNamespace(db=db)
            result = await extreme.fanout_recent_extreme_walmart_prices(bot)
            assert result["examined"] == 1
            assert result["eligible"] == 1
            assert result["posted"] == 1
            assert len(delivered) == 1
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["examined"] == 0
            await extreme.set_extreme_alerts_enabled(db, 295, False)
            assert not await extreme.extreme_alerts_enabled(db, 295)
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["posted"] == 0
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_health_summary_explains_qualified_vs_filtered_candidates_on_turso_rows() -> None:
    """The health panel must not mistake cheap accessories for extreme finds."""
    async def scenario():
        from dataclasses import replace

        conn = await aiosqlite.connect(":memory:")
        db = SimpleNamespace(require_conn=lambda: conn)
        try:
            await ensure_walmart_exact_verification_queue(db)
            await extreme.ensure_extreme_tables(db)
            current = datetime.now(timezone.utc).isoformat()
            phone = candidate()
            accessory = replace(
                phone,
                title="Galaxy S24 FE Case",
                product_id="29500012",
                sku="29500012",
                product_url="https://www.walmart.com/ip/29500012",
                direct_product_url="https://www.walmart.com/ip/29500012",
                variant_attributes={
                    **phone.variant_attributes,
                    "exactDetailItemId": "29500012",
                },
            )
            for product in (phone, accessory):
                await conn.execute(
                    f"INSERT INTO {QUEUE_TABLE} "
                    "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
                    "status, verified_at, exact_current_cents, snapshot_json) "
                    "VALUES (?, ?, ?, ?, 'verified_no_reference', ?, 100, ?)",
                    (
                        product.product_id, current, current, current,
                        current, _candidate_snapshot(product),
                    ),
                )
            await conn.commit()
            conn.row_factory = lambda cursor, values: {
                desc[0]: value for desc, value in zip(cursor.description, values)
            }
            summary = await extreme.extreme_price_health_summary(db)
            assert "Last 15 min" in summary
            assert "**2** exact-verified queue rows" in summary
            assert "**1** match primary-product" in summary
            assert "**1** are after the delivery cursor" in summary
            assert "not confirmed Discord posts" in summary
            assert "Not eligible: 1 accessory or parts" in summary
        finally:
            await conn.close()

    asyncio.run(scenario())



def test_extreme_rejection_reasons_match_sender_eligibility_without_softening() -> None:
    from dataclasses import replace

    base = candidate()
    cases = (
        (base, None),
        (replace(base, title="Samsung Galaxy S24 FE Case"), "accessory_or_parts"),
        (replace(base, title="Crest 3D White toothpaste"), "not_recognized_primary_product"),
        (replace(base, can_add_to_cart=False), "not_cart_buyable"),
        (replace(base, stock_status="Out of stock"), "unavailable_stock"),
        (replace(base, selected_offer_id=None), "offer_or_seller_proof_missing"),
        (
            replace(base, variant_attributes={**base.variant_attributes, "exactDetailPriceProof": "no"}),
            "exact_price_proof_missing",
        ),
        (
            replace(base, direct_product_url="https://www.walmart.com/ip/999", product_url="https://www.walmart.com/ip/999"),
            "item_url_mismatch",
        ),
        (replace(base, api_current_price=99), "price_mismatch_or_over_limit"),
        (replace(base, current_price=2), "price_mismatch_or_over_limit"),
        (
            replace(base, variant_attributes={**base.variant_attributes, "availableOnline": "false"}),
            "unavailable_online",
        ),
    )
    for original, expected in cases:
        reason = extreme.extreme_rejection_reason(original)
        assert reason == expected
        assert extreme.eligible_exact_extreme_candidate(original) is (reason is None)
    assert extreme.extreme_rejection_reason(None) == "missing_snapshot"


def test_qualified_extreme_item_waits_when_no_opted_in_live_route(monkeypatch) -> None:
    """A transient empty guild reconciliation cannot consume an unsent lead."""
    async def scenario():
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = SimpleNamespace(require_conn=lambda: conn)
        try:
            await ensure_walmart_exact_verification_queue(db)
            await extreme.set_extreme_alerts_enabled(db, 295, True)
            now = datetime.now(timezone.utc).isoformat()
            phone = candidate()
            await conn.execute(
                f"INSERT INTO {QUEUE_TABLE} "
                "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
                "status, verified_at, exact_current_cents, snapshot_json) "
                "VALUES (?, ?, ?, ?, 'verified_no_reference', ?, 100, ?)",
                (phone.product_id, now, now, now, now, _candidate_snapshot(phone)),
            )
            await conn.commit()

            reconciliations = 0
            async def live_route(_db, _bot, *, required_retailer):
                nonlocal reconciliations
                assert required_retailer == "walmart"
                reconciliations += 1
                if reconciliations == 1:
                    return LiveGuildLoadResult(guilds=())
                return LiveGuildLoadResult(guilds=(LiveAutoScanGuild(295, 123),))

            deliveries = []
            async def post(**kwargs):
                deliveries.append(kwargs)
                return SimpleNamespace(attempted=1, posted=1, errors=())

            monkeypatch.setattr(extreme, "list_live_public_alert_guilds", live_route)
            monkeypatch.setattr(extreme, "maybe_post_public_deal_cards", post)
            bot = SimpleNamespace(db=db)
            before_cursor = await conn.execute(
                f"SELECT last_verified_at, last_item_id FROM {extreme.STATE_TABLE} WHERE state_key='walmart'"
            )
            initial = tuple(await before_cursor.fetchone())
            stalled = await extreme.fanout_recent_extreme_walmart_prices(bot)
            assert stalled == {"examined": 1, "eligible": 1, "posted": 0}
            after_cursor = await conn.execute(
                f"SELECT last_verified_at, last_item_id FROM {extreme.STATE_TABLE} WHERE state_key='walmart'"
            )
            assert tuple(await after_cursor.fetchone()) == initial
            assert deliveries == []

            resumed = await extreme.fanout_recent_extreme_walmart_prices(bot)
            assert resumed == {"examined": 1, "eligible": 1, "posted": 1}
            assert len(deliveries) == 1
            assert deliveries[0]["guild_id"] == 295
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["examined"] == 0
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_missing_second_guild_replays_after_first_guild_cursor_advanced(monkeypatch) -> None:
    """Live guild A must not make a temporarily absent guild B lose the offer."""
    async def scenario():
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = lambda cursor, values: {
            col[0]: val for col, val in zip(cursor.description, values)
        }
        db = SimpleNamespace(require_conn=lambda: conn)
        try:
            await ensure_walmart_exact_verification_queue(db)
            await extreme.set_extreme_alerts_enabled(db, 295, True)
            await extreme.set_extreme_alerts_enabled(db, 296, True)
            now = datetime.now(timezone.utc).isoformat()
            phone = candidate()
            await conn.execute(
                f"INSERT INTO {QUEUE_TABLE} "
                "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
                "status, verified_at, exact_current_cents, snapshot_json) "
                "VALUES (?, ?, ?, ?, 'verified_no_reference', ?, 100, ?)",
                (phone.product_id, now, now, now, now, _candidate_snapshot(phone)),
            )
            await conn.commit()

            live_guilds = {295}
            deliveries = []
            async def fake_live(*_args, **_kwargs):
                return LiveGuildLoadResult(
                    guilds=tuple(LiveAutoScanGuild(g, g + 1000) for g in sorted(live_guilds))
                )
            async def fake_post(**kwargs):
                deliveries.append(kwargs["guild_id"])
                return SimpleNamespace(attempted=1, posted=1, errors=())

            monkeypatch.setattr(extreme, "list_live_public_alert_guilds", fake_live)
            monkeypatch.setattr(extreme, "maybe_post_public_deal_cards", fake_post)
            bot = SimpleNamespace(db=db)

            first = await extreme.fanout_recent_extreme_walmart_prices(bot)
            assert first == {"examined": 1, "eligible": 1, "posted": 1}
            assert deliveries == [295]
            read = await conn.execute(
                f"SELECT guild_id, item_id, verified_at FROM {extreme.PENDING_TABLE}"
            )
            queued = await read.fetchall()
            assert len(queued) == 1 and queued[0]["guild_id"] == "296"
            summary = await extreme.extreme_price_health_summary(db, guild_id=296)
            assert "Pending recovery for this server: **1**" in summary

            live_guilds.add(296)
            second = await extreme.fanout_recent_extreme_walmart_prices(bot)
            assert second["examined"] == 0
            assert second["posted"] == 1
            assert deliveries == [295, 296]
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["posted"] == 0
            read = await conn.execute(f"SELECT COUNT(*) AS n FROM {extreme.PENDING_TABLE}")
            assert (await read.fetchone())["n"] == 0
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_missing_guild_never_receives_changed_exact_price_and_optout_clears_queue(monkeypatch) -> None:
    from dataclasses import replace

    async def scenario():
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = SimpleNamespace(require_conn=lambda: conn)
        try:
            await ensure_walmart_exact_verification_queue(db)
            await extreme.set_extreme_alerts_enabled(db, 295, True)
            await extreme.set_extreme_alerts_enabled(db, 296, True)
            now = datetime.now(timezone.utc).isoformat()
            phone = candidate()
            await conn.execute(
                f"INSERT INTO {QUEUE_TABLE} "
                "(item_id, first_seen_at, last_seen_at, next_attempt_at, "
                "status, verified_at, exact_current_cents, snapshot_json) "
                "VALUES (?, ?, ?, ?, 'verified_no_reference', ?, 100, ?)",
                (phone.product_id, now, now, now, now, _candidate_snapshot(phone)),
            )
            await conn.commit()
            live = {295}
            sent = []
            async def fake_live(*_args, **_kwargs):
                return LiveGuildLoadResult(
                    guilds=tuple(LiveAutoScanGuild(g, g + 1000) for g in sorted(live))
                )
            async def fake_post(**kwargs):
                sent.append(kwargs["guild_id"])
                return SimpleNamespace(attempted=1, posted=1, errors=())
            monkeypatch.setattr(extreme, "list_live_public_alert_guilds", fake_live)
            monkeypatch.setattr(extreme, "maybe_post_public_deal_cards", fake_post)
            bot = SimpleNamespace(db=db)
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["posted"] == 1
            # Exact current price or offer changed after original verification.
            new_phone = replace(phone, api_current_price=20, current_price=20)
            await conn.execute(
                f"UPDATE {QUEUE_TABLE} SET verified_at = ?, snapshot_json = ?, "
                "exact_current_cents = 2000 WHERE item_id = ?",
                ((datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=1)).isoformat(),
                 _candidate_snapshot(new_phone), phone.product_id),
            )
            await conn.commit()
            live.add(296)
            assert (await extreme.fanout_recent_extreme_walmart_prices(bot))["posted"] == 0
            assert sent == [295]
            row = await conn.execute(f"SELECT COUNT(*) FROM {extreme.PENDING_TABLE}")
            assert (await row.fetchone())[0] == 0

            # Explicit opt-out is authoritative even if a replay ticket exists.
            await conn.execute(
                f"INSERT INTO {extreme.PENDING_TABLE} "
                "(guild_id,item_id,verified_at,snapshot_json) VALUES(?,?,?,?)",
                ("296", phone.product_id, now, _candidate_snapshot(phone)),
            )
            await conn.commit()
            await extreme.set_extreme_alerts_enabled(db, 296, False)
            row = await conn.execute(f"SELECT COUNT(*) FROM {extreme.PENDING_TABLE}")
            assert (await row.fetchone())[0] == 0
        finally:
            await conn.close()

    asyncio.run(scenario())
