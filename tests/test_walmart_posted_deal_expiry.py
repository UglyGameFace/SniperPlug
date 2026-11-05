"""Posted Walmart deals are edited in place when the offer ends.

Runs on the production-chain harness: real discovery, verification, lanes and
``maybe_post_public_deal_cards``; only Walmart HTTP and Discord are simulated.
"""

from __future__ import annotations

import pytest

from walmart_pipeline_harness import Pipeline, walmart_item

IPAD = walmart_item(9811, "Apple iPad mini 8.3-inch 128GB", 99.00, was_price=499.00,
                    category="Electronics/Tablets")


async def _posted(tmp_path, monkeypatch, item=IPAD, *, extreme=False, query="ipad mini",
                  mapping_rows=False):
    p = await Pipeline(tmp_path, monkeypatch, mapping_rows=mapping_rows).start()
    await p.enroll(p.guilds[0], threshold=40, extreme=extreme)
    p.api.add(dict(item), queries=(query,))
    await p.discover(query)
    await p.cycle()
    assert len(p.messages()) == 1
    return p


def _field_names(embed) -> list[str]:
    return [str(f.name) for f in embed.fields]


async def _state(p, item_id) -> str:
    from sniperplug.services.walmart_posted_deal_expiry import TABLE

    cursor = await p.db.require_conn().execute(
        f"SELECT state FROM {TABLE} WHERE item_id = ?", (str(item_id),)
    )
    row = await cursor.fetchone()
    return row["state"] if row else ""


@pytest.mark.parametrize("mapping_rows", [False, True], ids=["sqlite_rows", "turso_mapping_rows"])
@pytest.mark.asyncio
async def test_price_back_to_normal_edits_post_to_expired(tmp_path, monkeypatch, mapping_rows):
    p = await _posted(tmp_path, monkeypatch, mapping_rows=mapping_rows)
    try:
        p.api.set_price(IPAD["itemId"], 489.00)
        result = await p.sweep_expired_posts()
        assert result.api_calls == 1 and result.expired == 1
        message = p.messages()[0]
        assert message.embeds[0].title.startswith("⛔ EXPIRED")
        assert _field_names(message.embeds[0])[0] == "⛔ Deal ended"
        assert "$489.00" in message.embeds[0].fields[0].value
        assert "$99.00" in message.embeds[0].fields[0].value
        assert message.view is None  # buttons removed
        assert len(p.posts()) == 1  # edited in place, no new message
        assert await _state(p, IPAD["itemId"]) == "expired"
        cursor = await p.db.require_conn().execute(
            "SELECT status FROM guild_active_deal_cache WHERE guild_id = ?",
            (p.guilds[0].id,),
        )
        assert [row["status"] for row in await cursor.fetchall()] == ["expired"]
        # Idempotent: a later sweep neither re-edits nor re-checks it.
        again = await p.sweep_expired_posts()
        assert again.expired == 0 and message.edits == 1
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_sold_out_edits_post_to_expired(tmp_path, monkeypatch):
    p = await _posted(tmp_path, monkeypatch)
    try:
        p.api.items[str(IPAD["itemId"])]["stock"] = "Not available"
        await p.sweep_expired_posts()
        field = p.messages()[0].embeds[0].fields[0]
        assert field.name == "⛔ Deal ended" and "out of stock" in field.value
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_item_missing_from_lookup_is_not_treated_as_expired(tmp_path, monkeypatch):
    p = await _posted(tmp_path, monkeypatch)
    try:
        p.api.remove(IPAD["itemId"])
        result = await p.sweep_expired_posts()
        assert result.expired == 0
        assert not p.messages()[0].embeds[0].title.startswith("⛔")
        assert await _state(p, IPAD["itemId"]) == "active"
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_unchanged_or_small_price_moves_keep_post_active(tmp_path, monkeypatch):
    p = await _posted(tmp_path, monkeypatch)
    try:
        p.api.set_price(IPAD["itemId"], 101.00)  # +2%: same deal
        first = await p.sweep_expired_posts()
        assert first.api_calls == 1 and first.expired == 0
        # Checked recently: the paced lookup does not repeat within 2 hours.
        second = await p.sweep_expired_posts(make_stale=False)
        assert second.api_calls == 0
        assert p.messages()[0].edits == 0
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_exact_reverification_expires_post_without_extra_api_call(tmp_path, monkeypatch):
    p = await _posted(tmp_path, monkeypatch)
    try:
        p.api.set_price(IPAD["itemId"], 489.00)
        await p.discover("ipad mini")  # catalog price change wakes the exact row
        await p.cycle()
        lookups_before = p.api.count("lookup")
        result = await p.sweep_expired_posts(make_stale=False)
        assert result.expired == 1 and result.api_calls == 0
        assert p.api.count("lookup") == lookups_before
        assert p.messages()[0].embeds[0].title.startswith("⛔ EXPIRED")
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_deleted_message_stops_tracking_without_errors(tmp_path, monkeypatch):
    p = await _posted(tmp_path, monkeypatch)
    try:
        p.guilds[0].channel.messages.clear()  # a moderator deleted the post
        p.api.set_price(IPAD["itemId"], 489.00)
        result = await p.sweep_expired_posts()
        assert result.expired == 0
        assert await _state(p, IPAD["itemId"]) == "gone"
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_extreme_lane_post_is_also_retired(tmp_path, monkeypatch):
    tab = walmart_item(9812, 'Samsung Galaxy Tab S11 11" 128GB Wi-Fi Tablet', 1.00, msrp=799.99)
    p = await _posted(tmp_path, monkeypatch, tab, extreme=True, query="galaxy tab s11")
    try:
        assert p.messages()[0].embeds[0].title.startswith("Extreme exact-price find")
        p.api.set_price(tab["itemId"], 799.99)
        result = await p.sweep_expired_posts()
        assert result.expired == 1
        assert p.messages()[0].embeds[0].title.startswith("⛔ EXPIRED — Extreme")
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_sweep_is_self_paced(tmp_path, monkeypatch):
    from sniperplug.services.walmart_posted_deal_expiry import sweep_posted_walmart_deals

    p = await _posted(tmp_path, monkeypatch)
    try:
        # The worker cycle that posted the deal already ran a sweep, so further
        # cycles within 10 minutes skip it (no queries, no API calls).
        lookups = p.api.count("lookup")
        await p.cycle(3)
        skipped = await sweep_posted_walmart_deals(p.bot, provider=None)
        assert not skipped.ran
        assert p.api.count("lookup") == lookups
    finally:
        await p.close()
