from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import aiosqlite
import pytest

from sniperplug.services.ebay_browse_api import (
    EbayApiError, EbayBrowse, EbayListing, EbaySettings, _cents,
    _ebay_item_url, parse_listing,
)
from sniperplug.services.ebay_promotion_consent import (
    DISCLOSURE, guild_consent, may_promote, preview_text,
    safe_affiliate_link, save_guild_consent, save_user_consent,
    user_consented,
)


def _sample_payload(price="37.50", shipping="6.25", *, item_id="v1|1000|0"):
    return {
        "itemId": item_id, "title": "Sony genuine wireless controller",
        "itemWebUrl": "https://www.ebay.com/itm/1000",
        "itemAffiliateWebUrl": "https://www.ebay.com/itm/1000?mkevt=1&campid=1234567890",
        "buyingOptions": ["FIXED_PRICE"],
        "price": {"currency": "USD", "value": price},
        "condition": "New",
        "seller": {"username": "reliable-seller"},
        "shippingOptions": [{
            "shippingCost": {"currency": "USD", "value": shipping},
        }],
    }


def test_real_fixed_price_not_fake_sold_comp():
    item = parse_listing(_sample_payload())
    assert item is not None
    assert item.price_cents == 3750
    assert item.shipping_cents == 625
    assert item.delivered_cents == 4375
    assert item.asking_url == "https://www.ebay.com/itm/1000"
    assert "ASKING" in preview_text(item, approved=False)
    assert "Not a sold comparable" in preview_text(item, approved=False)
    assert "DEMO ONLY" in preview_text(item, approved=False)


def test_auc_currency_link_and_price_errors_rejected():
    auction = _sample_payload()
    auction["buyingOptions"] = ["AUCTION"]
    assert parse_listing(auction) is None

    foreign = _sample_payload()
    foreign["price"]["currency"] = "EUR"
    assert parse_listing(foreign) is None

    unsafe = _sample_payload()
    unsafe["itemWebUrl"] = "https://evil.example/itm/123"
    assert parse_listing(unsafe) is None

    for invalid in ("nan", "inf", "-1", "0", "3.555", "10000001", None):
        assert _cents(invalid) is None or invalid == "0"

    missing_ship = _sample_payload()
    missing_ship["shippingOptions"] = []
    item = parse_listing(missing_ship)
    assert item and item.shipping_cents is None and item.delivered_cents is None
    assert "unknown" in preview_text(item, approved=False)


@pytest.mark.parametrize("link", [
    "http://www.ebay.com/itm/1000",
    "https://evil.com/itm/1000",
    "https://ebay.com.evil.com/itm/1000",
    "https://api.ebay.com/buy/browse/v1/item/123",
    "https://user:password@ebay.com/itm/123",
    "https://ebay.com:invalid/itm/123",
    "https://ebay.com:8080/itm/123",
])
def test_untrusted_ebay_url_fails_closed(link):
    assert _ebay_item_url(link) == ""


def test_marketing_approval_requires_all_independent_gates():
    listing = parse_listing(_sample_payload())
    assert listing is not None
    ready = EbaySettings(
        client_id="production-client", client_secret="secret",
        campaign_id="1234567890", research_enabled=True,
        messaging_approved=True,
    )
    assert may_promote(ready, opted_in=True, channel_matches=True)
    assert safe_affiliate_link(ready, listing, opted_in=True) == listing.affiliate_url
    foreign_campaign = replace(listing, affiliate_url="https://www.ebay.com/itm/1000?campid=9999999999")
    assert safe_affiliate_link(ready, foreign_campaign, opted_in=True) == ""
    no_campaign = replace(listing, affiliate_url="https://www.ebay.com/itm/1000")
    assert safe_affiliate_link(ready, no_campaign, opted_in=True) == ""
    assert not may_promote(ready, opted_in=False)
    assert not may_promote(ready, opted_in=True, channel_matches=False)
    for disabled in (
        replace(ready, messaging_approved=False),
        replace(ready, research_enabled=False),
        replace(ready, client_secret=""),
        replace(ready, sandbox=True),
        replace(ready, campaign_id=""),
        replace(ready, campaign_id="short"),
    ):
        assert not may_promote(disabled, opted_in=True)
        assert safe_affiliate_link(disabled, listing, opted_in=True) == ""


def test_disclosure_is_present_and_no_fabricated_sale():
    listing = parse_listing(_sample_payload())
    assert listing is not None
    assert "commission" in DISCLOSURE.lower()
    sample = preview_text(listing, approved=True)
    assert DISCLOSURE in sample
    assert "asking" in sample.lower()
    assert "profit" in sample.lower()


def test_opt_in_unsubscribe_and_guild_channel_consent_persist_on_local_sqlite():
    async def run():
        conn = await aiosqlite.connect(":memory:")
        db = SimpleNamespace(require_conn=lambda: conn)
        try:
            assert not await user_consented(db, 123456789012345678)
            await save_user_consent(db, 123456789012345678, True)
            assert await user_consented(db, 123456789012345678)
            await save_user_consent(db, 123456789012345678, False)
            assert not await user_consented(db, 123456789012345678)
            cursor = await conn.execute("SELECT COUNT(*) FROM ebay_dm_promotion_consent")
            assert (await cursor.fetchone())[0] == 0

            first = await guild_consent(db, 223456789012345678)
            assert not first.enabled and first.channel_id is None
            await save_guild_consent(
                db, guild_id=223456789012345678,
                actor_id=323456789012345678,
                channel_id=423456789012345678, enabled=True,
            )
            confirmed = await guild_consent(db, 223456789012345678)
            assert confirmed.enabled and confirmed.channel_id == 423456789012345678
            await save_guild_consent(
                db, guild_id=223456789012345678,
                actor_id=323456789012345678,
                channel_id=None, enabled=False,
            )
            stopped = await guild_consent(db, 223456789012345678)
            assert not stopped.enabled and stopped.channel_id is None
        finally:
            await conn.close()
    asyncio.run(run())


def test_authenticated_browse_uses_only_official_origin_and_one_token(monkeypatch):
    async def run():
        client = EbayBrowse(EbaySettings(
            client_id="key", client_secret="secret", research_enabled=True,
        ))
        calls = []
        async def fake_json(_session, method, url, *, headers, params=None, form=None):
            calls.append((method, url, dict(headers), params, form))
            if "/identity/v1/" in url:
                return {"access_token": "TEST_ONLY_TOKEN", "expires_in": 7200}, 200
            return {"itemSummaries": [_sample_payload()]}, 200
        monkeypatch.setattr(client, "_json", fake_json)
        result = await client.search(query="genuine controller")
        again = await client.search(query="genuine controller")
        assert len(result) == len(again) == 1
        assert len([c for c in calls if "/identity/v1/" in c[1]]) == 1
        browse_calls = [c for c in calls if "/buy/browse/v1/" in c[1]]
        assert len(browse_calls) == 2
        assert all(c[1] == "https://api.ebay.com/buy/browse/v1/item_summary/search" for c in browse_calls)
        assert all(c[2]["Authorization"] == "Bearer TEST_ONLY_TOKEN" for c in browse_calls)
        assert all("X-EBAY-C-ENDUSERCTX" not in c[2] for c in browse_calls)
        assert all(c[3]["filter"] == "buyingOptions:{FIXED_PRICE},deliveryCountry:US" for c in browse_calls)
    asyncio.run(run())


def test_research_disabled_and_invalid_gtin_never_call_network():
    async def run():
        client = EbayBrowse(EbaySettings(client_id="a", client_secret="b"))
        with pytest.raises(EbayApiError, match="disabled"):
            await client.search(query="phone")
        enabled = EbayBrowse(EbaySettings(client_id="a", client_secret="b", research_enabled=True))
        with pytest.raises(EbayApiError, match="GTIN"):
            await enabled.search(gtin="unknown")
        with pytest.raises(EbayApiError, match="exactly one"):
            await enabled.search(query="phone", gtin="012345678905")
    asyncio.run(run())


def test_no_old_standalone_worker_in_canonical_repo():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert not (root / "sniperplug/ebay_watcher").exists()
    assert not (root / "discloud.ebay-watcher.config").exists()
    assert not (root / "ebay_watcher_main.py").exists()
    bot = (root / "sniperplug/bot.py").read_text()
    assert "await self.add_cog(EbayResearchCog(self))" in bot
    assert "from sniperplug.cogs.ebay_research import EbayResearchCog" in bot



def test_unapproved_campaign_never_appears_in_research_http_header(monkeypatch):
    async def run():
        client = EbayBrowse(EbaySettings(
            client_id="key", client_secret="secret",
            campaign_id="1234567890", research_enabled=True,
            messaging_approved=False,
        ))
        calls = []
        async def fake_json(_session, method, url, *, headers, params=None, form=None):
            calls.append((method, url, dict(headers)))
            if "/identity/v1/" in url:
                return {"access_token": "token", "expires_in": 7200}, 200
            return {"itemSummaries": [_sample_payload()]}, 200
        monkeypatch.setattr(client, "_json", fake_json)
        await client.search(query="headphones")
        browse = [x for x in calls if "/buy/browse/" in x[1]]
        assert browse
        assert all("X-EBAY-C-ENDUSERCTX" not in x[2] for x in browse)

        approved = EbayBrowse(replace(client.settings, messaging_approved=True))
        calls.clear()
        monkeypatch.setattr(approved, "_json", fake_json)
        await approved.search(query="headphones")
        browse = [x for x in calls if "/buy/browse/" in x[1]]
        assert browse[0][2]["X-EBAY-C-ENDUSERCTX"] == "affiliateCampaignId=1234567890"
    asyncio.run(run())
