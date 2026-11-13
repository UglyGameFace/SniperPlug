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


def test_logitech_h390_reported_black_price_does_not_leave_old_quote_current():
    from sniperplug.services.walmart_posted_deal_expiry import _lookup_evidence

    # The owner's screenshot shows a $29.56 old Discord alert while Walmart
    # displays $24.88 for black. A product-level lookup is not a seller/variant
    # fingerprint; flag the old quote, never claim the new one as the same offer.
    result = _lookup_evidence(
        {"salePrice": 24.88, "stock": "Available", "availableOnline": True},
        2_956,
    )
    assert result is not None
    reason, current = result
    assert current == 2_488
    assert "lower price of $24.88" in reason
    assert "different seller or selected variant" in reason


@pytest.mark.asyncio
async def test_materially_lower_walmart_price_marks_old_quote_for_recheck(tmp_path, monkeypatch):
    """A cheaper product-page quote invalidates the old live-looking price."""
    p = await _posted(tmp_path, monkeypatch)
    try:
        p.api.set_price(IPAD["itemId"], 79.00)
        result = await p.sweep_expired_posts()
        assert result.api_calls == 1 and result.expired == 1
        message = p.messages()[0]
        assert message.embeds[0].title.startswith("⚠️ RECHECK PRICE")
        first = message.embeds[0].fields[0]
        assert first.name == "⚠️ Recheck current price"
        assert "$79.00" in first.value and "$99.00" in first.value
        assert "different seller or selected variant" in first.value
        assert message.view is None
        assert len(p.posts()) == 1
        assert await _state(p, IPAD["itemId"]) == "expired"
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_small_price_drop_does_not_relabel_current_post(tmp_path, monkeypatch):
    p = await _posted(tmp_path, monkeypatch)
    try:
        p.api.set_price(IPAD["itemId"], 97.00)
        result = await p.sweep_expired_posts()
        assert result.api_calls == 1 and result.expired == 0
        assert p.messages()[0].edits == 0
        assert await _state(p, IPAD["itemId"]) == "active"
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


@pytest.mark.asyncio
async def test_expired_edit_stays_inside_discord_embed_limits(tmp_path, monkeypatch):
    p = await _posted(tmp_path, monkeypatch)
    try:
        embed = p.messages()[0].embeds[0]
        embed.description = "x" * 4000
        while len(embed.fields) < 25:
            embed.add_field(name=f"Field {len(embed.fields)}", value="y" * 200, inline=True)
        p.api.set_price(IPAD["itemId"], 489.00)
        result = await p.sweep_expired_posts()
        assert result.expired == 1
        edited = p.messages()[0].embeds[0]
        assert len(edited.fields) <= 25
        assert len(edited) <= 6000
        assert edited.fields[0].name == "⛔ Deal ended"
        assert edited.title.startswith("⛔ EXPIRED")
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_rejected_edit_falls_back_to_minimal_expired_embed(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import discord

    p = await _posted(tmp_path, monkeypatch)
    try:
        message = p.messages()[0]
        original_edit = message.edit
        calls = {"n": 0}

        async def flaky_edit(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise discord.HTTPException(SimpleNamespace(status=400, reason="Bad Request"), "Invalid Form Body")
            return await original_edit(**kwargs)

        message.edit = flaky_edit
        p.api.set_price(IPAD["itemId"], 489.00)
        result = await p.sweep_expired_posts()
        assert result.expired == 1 and calls["n"] == 2
        edited = message.embeds[0]
        assert edited.title.startswith("⛔ EXPIRED") and [f.name for f in edited.fields] == ["⛔ Deal ended"]
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_deal_that_returns_after_expiry_is_posted_again(tmp_path, monkeypatch):
    p = await Pipeline(tmp_path, monkeypatch, guild_ids=(111000, 222000)).start()
    try:
        for guild in p.guilds:
            await p.enroll(guild, threshold=40)
        p.api.add(dict(IPAD), queries=("ipad mini",))
        await p.discover("ipad mini")
        await p.cycle()
        p.api.set_price(IPAD["itemId"], 489.00)
        await p.discover("ipad mini")
        await p.cycle()
        assert (await p.sweep_expired_posts()).expired == 2
        # Still at full price: nothing new may be posted.
        await p.cycle(2)
        assert [len(p.posts(g)) for g in p.guilds] == [1, 1]
        # The same $99 deal comes back (restock / second price drop).
        p.api.set_price(IPAD["itemId"], 99.00)
        await p.discover("ipad mini")
        await p.cycle()
        assert [len(p.posts(g)) for g in p.guilds] == [2, 2]
        for guild in p.guilds:
            assert p.messages(guild)[0].embeds[0].title.startswith("⛔ EXPIRED")
            assert not p.messages(guild)[1].embeds[0].title.startswith("⛔")
        # And it is not posted a third time on the next cycles.
        await p.cycle(2)
        assert [len(p.posts(g)) for g in p.guilds] == [2, 2]
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_extreme_lead_that_returns_after_expiry_is_posted_again(tmp_path, monkeypatch):
    tab = walmart_item(9813, 'Samsung Galaxy Tab S11 11" 256GB Wi-Fi Tablet', 1.00, msrp=899.99)
    p = await _posted(tmp_path, monkeypatch, tab, extreme=True, query="galaxy tab s11 256")
    try:
        p.api.set_price(tab["itemId"], 899.99)
        await p.discover("galaxy tab s11 256")
        await p.cycle()
        assert (await p.sweep_expired_posts()).expired == 1
        await p.cycle(2)
        assert len(p.posts()) == 1
        p.api.set_price(tab["itemId"], 1.00)
        await p.discover("galaxy tab s11 256")
        await p.cycle()
        assert len(p.posts()) == 2
        await p.cycle(2)
        assert len(p.posts()) == 2
    finally:
        await p.close()
