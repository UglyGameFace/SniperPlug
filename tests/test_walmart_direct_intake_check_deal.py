from __future__ import annotations

import asyncio
import copy
import json
import threading

import aiosqlite
import pytest

from sniperplug.cogs.deal_scanner import build_walmart_cards
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult
from sniperplug.providers.walmart import (
    WalmartAffiliateConfig,
    WalmartProvider,
    WalmartProviderError,
)
from sniperplug.providers.walmart_page_recovery import WalmartPageRecoveryProvider
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    parse_walmart_item_id,
    verify_single_exact_item,
)
from sniperplug.services.walmart_price_sniper_routes import (
    PRICE_SNIPER_QUERIES,
    price_sniper_query_for_batch,
)


IPHONE_ITEM_ID = "425031866"
IPHONE_URL = (
    "https://www.walmart.com/ip/AT-T-Apple-iPhone-11-64GB-Green/425031866"
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class RecordingInner:
    def __init__(self):
        self.thread_ids: list[int] = []
        self.delegate = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )

    def _candidate_from_item(self, item, *, request):
        self.thread_ids.append(threading.get_ident())
        return self.delegate._candidate_from_item(item, request=request)


class FakeDetailProvider:
    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.inner = RecordingInner()
        self.calls: list[str] = []

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        self.calls.append(item_id)
        return self.payloads[item_id]


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


def iphone_payload(*, price: float, reference: dict | None = None) -> dict:
    payload: dict[str, object] = {
        "itemId": int(IPHONE_ITEM_ID),
        "name": "Apple iPhone 11 - AT&T - 64GB Green",
        "salePrice": price,
        "isMarketPlaceItem": False,
        "availableOnline": True,
    }
    if reference:
        payload.update(reference)
    return payload


async def queue_row(conn, item_id: str):
    cursor = await conn.execute(
        f"SELECT status, exact_current_cents, exact_reference_cents, "
        f"discovered_count, source_label FROM {QUEUE_TABLE} WHERE item_id = ?",
        (item_id,),
    )
    return await cursor.fetchone()


def test_parse_walmart_item_id_accepts_real_iphone_url() -> None:
    assert parse_walmart_item_id(IPHONE_URL) == IPHONE_ITEM_ID


def test_parse_walmart_item_id_accepts_bare_id_and_variants() -> None:
    assert parse_walmart_item_id(IPHONE_ITEM_ID) == IPHONE_ITEM_ID
    assert parse_walmart_item_id(f"  {IPHONE_ITEM_ID}  ") == IPHONE_ITEM_ID
    assert (
        parse_walmart_item_id(f"https://www.walmart.com/ip/{IPHONE_ITEM_ID}")
        == IPHONE_ITEM_ID
    )
    assert (
        parse_walmart_item_id(f"{IPHONE_URL}?athbdg=L1600")
        == IPHONE_ITEM_ID
    )
    assert (
        parse_walmart_item_id(f"http://walmart.com/ip/Some-Phone/{IPHONE_ITEM_ID}/")
        == IPHONE_ITEM_ID
    )


def test_parse_walmart_item_id_rejects_non_walmart_and_garbage() -> None:
    assert parse_walmart_item_id("") is None
    assert parse_walmart_item_id("iphone 11") is None
    assert parse_walmart_item_id("425031866x") is None
    assert parse_walmart_item_id("https://www.amazon.com/dp/B08N5WRWNW") is None
    assert (
        parse_walmart_item_id("https://www.walmart.com/ip/Some-Phone-No-Id")
        is None
    )
    assert parse_walmart_item_id("https://www.walmart.com/search?q=iphone") is None
    assert parse_walmart_item_id("9" * 21) is None


@pytest.mark.asyncio
async def test_verify_single_exact_item_marks_one_dollar_iphone_a_verified_markdown() -> None:
    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    provider = FakeDetailProvider(
        {
            IPHONE_ITEM_ID: iphone_payload(
                price=1.00, reference={"wasPrice": 650.00}
            )
        }
    )

    result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)

    assert result.status == "verified"
    assert result.classification == "verified_markdown"
    assert result.enqueued is True
    assert result.queued_unique == 1
    assert provider.calls == [IPHONE_ITEM_ID]
    candidate = result.candidate
    assert candidate is not None
    assert candidate.api_current_price == 1.00
    assert candidate.api_reference_price == 650.00
    assert candidate.title == "Apple iPhone 11 - AT&T - 64GB Green"

    row = await queue_row(conn, IPHONE_ITEM_ID)
    assert row is not None
    assert row[0] == "verified_markdown"
    assert row[1] == 100
    assert row[2] == 65000
    assert row[4] == "manual_link"

    scan_result = ProviderScanResult(
        provider_key="walmart",
        candidates=(candidate,),
        warnings=(),
        total_results=1,
        page=1,
        page_size=1,
        start_index=1,
        has_next_page=False,
    )
    cards = build_walmart_cards(scan_result, min_discount=40, alerts_only=False)
    assert len(cards) == 1
    assert 99.0 < cards[0].discount < 100.0
    assert cards[0].api_current_price == 1.00
    await conn.close()


@pytest.mark.asyncio
async def test_verify_single_exact_item_msrp_only_is_no_reference() -> None:
    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    provider = FakeDetailProvider(
        {IPHONE_ITEM_ID: iphone_payload(price=1.00, reference={"msrp": 650.00})}
    )

    result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)

    assert result.status == "verified"
    # MSRP is never trusted markdown proof, so a $1 price against a $650
    # list price is a no-reference lead for the extreme lane, not a markdown.
    assert result.classification == "verified_no_reference"
    row = await queue_row(conn, IPHONE_ITEM_ID)
    assert row is not None
    assert row[0] == "verified_no_reference"
    await conn.close()


@pytest.mark.asyncio
async def test_verify_single_exact_item_without_price_retries_with_exact_reason() -> None:
    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    provider = FakeDetailProvider(
        {
            IPHONE_ITEM_ID: {
                "itemId": int(IPHONE_ITEM_ID),
                "name": "Apple iPhone 11 - AT&T - 64GB Green",
                # Explicitly non-marketplace, so Walmart is the seller and the
                # identity check passes; only the price is missing.
                "isMarketPlaceItem": False,
                "availableOnline": True,
            }
        }
    )

    result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)

    assert result.candidate is None
    assert result.status == "retry"
    assert "numeric current price" in result.error
    row = await queue_row(conn, IPHONE_ITEM_ID)
    assert row is not None
    assert row[0] == "retry"
    await conn.close()


@pytest.mark.asyncio
async def test_verify_single_exact_item_recovers_seller_identity_from_product_page() -> None:
    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    payload = {
        "itemId": int(IPHONE_ITEM_ID),
        "name": "Apple iPhone 11 - AT&T - 64GB Green",
        "salePrice": 1.00,
        "wasPrice": 650.00,
        "availableOnline": True,
    }
    scripts = [
        jsonld_product(
            item_id=IPHONE_ITEM_ID,
            price=1.00,
            seller="Walmart",
            seller_id="F55CDC31AB754BB68FE0B39041159D63",
            offer_id="OFFER123",
        )
    ]
    provider = FixturePageProvider(payload, scripts)

    result = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)

    assert provider.page_calls == 1
    assert result.status == "verified"
    assert result.classification == "verified_markdown"
    assert result.candidate is not None
    assert result.candidate.seller_name == "Walmart"
    row = await queue_row(conn, IPHONE_ITEM_ID)
    assert row is not None
    assert row[0] == "verified_markdown"
    await conn.close()


@pytest.mark.asyncio
async def test_verify_single_exact_item_does_not_duplicate_queue_rows() -> None:
    conn = await aiosqlite.connect(":memory:")
    db = FakeDatabase(conn)
    provider = FakeDetailProvider(
        {
            IPHONE_ITEM_ID: iphone_payload(
                price=1.00, reference={"wasPrice": 650.00}
            )
        }
    )

    first = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)
    second = await verify_single_exact_item(db, provider, IPHONE_ITEM_ID)

    assert first.status == "verified"
    assert second.status == "verified"
    assert provider.calls == [IPHONE_ITEM_ID, IPHONE_ITEM_ID]
    cursor = await conn.execute(
        f"SELECT COUNT(*), source_label FROM {QUEUE_TABLE} "
        f"WHERE item_id = ?",
        (IPHONE_ITEM_ID,),
    )
    count_row = await cursor.fetchone()
    assert count_row is not None
    # One queue row for the item: the repeat submit re-verifies it but the
    # bulk enqueue deliberately suppresses a no-op rewrite of an unchanged
    # verified row, so no duplicate row is ever created.
    assert count_row[0] == 1
    assert count_row[1] == "manual_link"
    await conn.close()


@pytest.mark.asyncio
async def test_verify_single_exact_item_rejects_non_numeric_id() -> None:
    result = await verify_single_exact_item(None, None, "iphone-11")
    assert result.candidate is None
    assert result.status == "invalid_item_id"


@pytest.mark.asyncio
async def test_verify_single_exact_item_without_database_still_verifies() -> None:
    provider = FakeDetailProvider(
        {
            IPHONE_ITEM_ID: iphone_payload(
                price=1.00, reference={"wasPrice": 650.00}
            )
        }
    )

    result = await verify_single_exact_item(None, provider, IPHONE_ITEM_ID)

    assert result.status == "verified"
    assert result.classification == "verified_markdown"
    assert result.enqueued is False


def test_price_sniper_rotation_covers_new_iphone_queries() -> None:
    assert "iphone" in PRICE_SNIPER_QUERIES
    assert "apple iphone" in PRICE_SNIPER_QUERIES
    unlocked_index = PRICE_SNIPER_QUERIES.index("unlocked smartphone")
    assert PRICE_SNIPER_QUERIES[unlocked_index + 1] == "iphone"
    assert PRICE_SNIPER_QUERIES[unlocked_index + 2] == "apple iphone"

    seen = [
        price_sniper_query_for_batch(batch)
        for batch in range(len(PRICE_SNIPER_QUERIES))
    ]
    assert sorted(seen) == sorted(PRICE_SNIPER_QUERIES)
    assert len(set(seen)) == len(PRICE_SNIPER_QUERIES)
