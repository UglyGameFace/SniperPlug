"""Home Depot penny tracker: storage, transitions, poll cycle, seeding.

The poll cycle is driven with a fake product-detail fetch so no SerpApi
credit is ever spent in tests. Event publication is asserted against the
real verified-retailer outbox table the fanout claims from.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.hd_penny_stages import PennyStage
from sniperplug.services.hd_penny_tracker import (
    EVENT_TYPE_FINAL,
    EVENT_TYPE_PENNY,
    HdPennyConfig,
    ensure_hd_penny_tables,
    get_hd_penny_item,
    learned_stage_dwell_days,
    list_hd_penny_items,
    load_hd_penny_status,
    record_hd_penny_observation,
    run_hd_penny_poll_cycle,
    seed_from_clearance_seed,
    seed_from_hunt_candidates,
    stage_dwells_from_history,
    track_hd_penny_item,
)
from sniperplug.services.home_depot_product_lookup import HomeDepotProductDetail
from sniperplug.services.verified_retailer_events import claim_verified_retailer_events


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


async def fresh_db() -> FakeDatabase:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    return FakeDatabase(conn)


def detail(product_id: str, price: float | None, original: float | None = None):
    return HomeDepotProductDetail(
        product_id=product_id,
        title="Tracked item",
        price=price,
        original_price=original,
        reference_price_source="original_price" if original else None,
    )


def test_track_dedupes_by_product_and_store_and_records_history() -> None:
    async def run() -> None:
        db = await fresh_db()
        item, created = await track_hd_penny_item(
            db,
            product_id="100123456",
            title="Faucet",
            product_url="https://www.homedepot.com/p/100123456",
            price_cents=2506,
            reference_cents=9999,
            source="penny_hunt",
            now=T0,
        )
        assert created is True
        assert item.stage == PennyStage.MID
        again, created_again = await track_hd_penny_item(
            db,
            product_id="100123456",
            title="Faucet",
            product_url="https://www.homedepot.com/p/100123456",
            price_cents=2506,
            reference_cents=9999,
            source="penny_hunt",
            now=T0 + timedelta(hours=1),
        )
        assert created_again is False
        assert again.item_id == item.item_id
        other_store, created_other = await track_hd_penny_item(
            db,
            product_id="100123456",
            title="Faucet",
            product_url="https://www.homedepot.com/p/100123456",
            store_id="1234",
            price_cents=2506,
            now=T0,
        )
        assert created_other is True
        assert other_store.item_id != item.item_id

        conn = db.require_conn()
        cursor = await conn.execute("SELECT COUNT(*) AS n FROM hd_penny_price_history")
        row = await cursor.fetchone()
        assert row["n"] == 2  # one per distinct tracked item, no dup on re-track

    asyncio.run(run())


def test_observation_transitions_and_penny_status() -> None:
    async def run() -> None:
        db = await fresh_db()
        item, _ = await track_hd_penny_item(
            db,
            product_id="100123456",
            title="Vanity",
            product_url="https://www.homedepot.com/p/100123456",
            price_cents=2506,
            reference_cents=9999,
            now=T0,
        )
        no_move = await record_hd_penny_observation(
            db, item_id=item.item_id, price_cents=2406, now=T0 + timedelta(days=1)
        )
        assert no_move is None  # still mid stage (.06 -> .06 ending family)

        final = await record_hd_penny_observation(
            db, item_id=item.item_id, price_cents=1003, now=T0 + timedelta(days=2)
        )
        assert final is not None
        assert final.entered_final is True
        assert final.entered_penny is False

        penny = await record_hd_penny_observation(
            db, item_id=item.item_id, price_cents=1, now=T0 + timedelta(days=9)
        )
        assert penny is not None
        assert penny.entered_penny is True
        stored = await get_hd_penny_item(db, item.item_id)
        assert stored is not None
        assert stored.status == "pennied"
        assert stored.stage == PennyStage.PENNY
        assert stored.reference_cents == 9999  # reference never lost

    asyncio.run(run())


def test_poll_cycle_publishes_final_then_penny_events_once(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    async def run() -> None:
        db = await fresh_db()
        item, _ = await track_hd_penny_item(
            db,
            product_id="100123456",
            title="Ceiling fan",
            product_url="https://www.homedepot.com/p/100123456",
            price_cents=2506,
            reference_cents=9999,
            now=T0,
        )
        prices = {"value": detail("100123456", 25.03, 99.99)}

        async def fake_fetch(product_id, *, zip_code=None, store_id=None, db=None):
            return prices["value"]

        config = HdPennyConfig(enabled=True, poll_interval_hours=6, daily_poll_limit=30)
        first = await run_hd_penny_poll_cycle(
            db, fetch_detail=fake_fetch, config=config, now=T0 + timedelta(hours=7)
        )
        assert first.polled == 1
        assert first.events_published == 1

        conn = db.require_conn()
        cursor = await conn.execute(
            "SELECT retailer, event_type, event_key FROM retailer_verified_deal_events"
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        assert len(rows) == 1
        assert rows[0]["retailer"] == "home_depot"
        assert rows[0]["event_type"] == EVENT_TYPE_FINAL

        # Same stage on the next due poll: no second event.
        second = await run_hd_penny_poll_cycle(
            db, fetch_detail=fake_fetch, config=config, now=T0 + timedelta(hours=14)
        )
        assert second.events_published == 0

        # The drop to a penny publishes the penny event and ends polling.
        prices["value"] = detail("100123456", 0.01, 99.99)
        third = await run_hd_penny_poll_cycle(
            db, fetch_detail=fake_fetch, config=config, now=T0 + timedelta(hours=21)
        )
        assert third.events_published == 1
        cursor = await conn.execute(
            "SELECT event_type FROM retailer_verified_deal_events"
        )
        event_types = [row["event_type"] for row in await cursor.fetchall()]
        assert sorted(event_types) == [EVENT_TYPE_FINAL, EVENT_TYPE_PENNY]

        stored = await get_hd_penny_item(db, item.item_id)
        assert stored is not None and stored.status == "pennied"
        fourth = await run_hd_penny_poll_cycle(
            db, fetch_detail=fake_fetch, config=config, now=T0 + timedelta(hours=28)
        )
        assert fourth.polled == 0  # pennied items leave the poll rotation

        # The fanout can claim the events and read their snapshots back.
        claimed = await claim_verified_retailer_events(db, limit=10)
        assert len(claimed) == 2
        penny_claim = [c for c in claimed if c.event_type == EVENT_TYPE_PENNY][0]
        assert penny_claim.candidate.api_current_price == 0.01
        assert penny_claim.candidate.variant_attributes["hdPennyProof"] == "yes"

    asyncio.run(run())


def test_poll_cycle_without_key_degrades_honestly(monkeypatch) -> None:
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    async def run() -> None:
        db = await fresh_db()
        await track_hd_penny_item(
            db,
            product_id="100123456",
            title="Faucet",
            product_url="https://www.homedepot.com/p/100123456",
            price_cents=2506,
            now=T0,
        )

        async def fake_fetch(product_id, *, zip_code=None, store_id=None, db=None):
            raise AssertionError("fetch must not run without a key")

        summary = await run_hd_penny_poll_cycle(
            db,
            fetch_detail=fake_fetch,
            config=HdPennyConfig(enabled=True),
            now=T0 + timedelta(hours=7),
        )
        assert summary.key_configured is False
        assert summary.skipped_reason is not None
        assert summary.polled == 0

        status = await load_hd_penny_status(db, now=T0)
        assert status.key_configured is False
        assert status.watching == 1

    asyncio.run(run())


def test_poll_cycle_respects_daily_cap_and_final_stage_priority(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    async def run() -> None:
        db = await fresh_db()
        await track_hd_penny_item(
            db,
            product_id="111",
            title="Mid item",
            product_url="https://www.homedepot.com/p/111",
            price_cents=2506,
            reference_cents=9999,
            now=T0,
        )
        final_item, _ = await track_hd_penny_item(
            db,
            product_id="222",
            title="Final item",
            product_url="https://www.homedepot.com/p/222",
            price_cents=1003,
            reference_cents=9999,
            now=T0,
        )
        fetched: list[str] = []

        async def fake_fetch(product_id, *, zip_code=None, store_id=None, db=None):
            fetched.append(product_id)
            return detail(product_id, 10.03, 99.99)

        summary = await run_hd_penny_poll_cycle(
            db,
            fetch_detail=fake_fetch,
            config=HdPennyConfig(enabled=True, poll_interval_hours=6, daily_poll_limit=1),
            now=T0 + timedelta(hours=7),
        )
        assert summary.polled == 1
        assert fetched == ["222"]  # final stage wins the single daily credit

        stored_final = await get_hd_penny_item(db, final_item.item_id)
        assert stored_final is not None
        assert stored_final.last_polled_at is not None

    asyncio.run(run())


def test_poll_cycle_records_failures_without_history(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")

    async def run() -> None:
        db = await fresh_db()
        item, _ = await track_hd_penny_item(
            db,
            product_id="100123456",
            title="Faucet",
            product_url="https://www.homedepot.com/p/100123456",
            price_cents=2506,
            now=T0,
        )

        async def fake_fetch(product_id, *, zip_code=None, store_id=None, db=None):
            return detail(product_id, None)  # SerpApi returned no price

        summary = await run_hd_penny_poll_cycle(
            db,
            fetch_detail=fake_fetch,
            config=HdPennyConfig(enabled=True, poll_interval_hours=6, daily_poll_limit=30),
            now=T0 + timedelta(hours=7),
        )
        assert summary.failures == 1
        assert summary.events_published == 0
        stored = await get_hd_penny_item(db, item.item_id)
        assert stored is not None
        assert stored.consecutive_failures == 1
        assert stored.price_cents == 2506  # last real price stands
        conn = db.require_conn()
        cursor = await conn.execute("SELECT COUNT(*) AS n FROM hd_penny_price_history")
        assert (await cursor.fetchone())["n"] == 1

    asyncio.run(run())


def _hunt_candidate(**overrides) -> SourceCandidate:
    base = dict(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Hunt result",
        product_url="https://www.homedepot.com/p/100123456",
        current_price=25.06,
        typical_price=99.99,
        product_id="100123456",
    )
    base.update(overrides)
    return SourceCandidate(**base)


def test_seed_from_hunt_candidates_filters_by_stage_or_depth() -> None:
    async def run() -> None:
        db = await fresh_db()
        result = await seed_from_hunt_candidates(
            db,
            [
                _hunt_candidate(),  # .06 mid stage -> tracked
                _hunt_candidate(  # full price, no stage -> skipped
                    product_id="200",
                    product_url="https://www.homedepot.com/p/200",
                    current_price=49.99,
                    typical_price=49.99,
                ),
                _hunt_candidate(  # off-ladder ending but 75% off -> tracked
                    product_id="300",
                    product_url="https://www.homedepot.com/p/300",
                    current_price=8.33,
                    typical_price=32.98,
                ),
                _hunt_candidate(product_id=None),  # no identity -> skipped
            ],
            guild_id=42,
            now=T0,
        )
        assert result.tracked_new == 2
        assert result.skipped == 2
        items = await list_hd_penny_items(db)
        assert {item.product_id for item in items} == {"100123456", "300"}
        off_ladder = [item for item in items if item.product_id == "300"][0]
        assert off_ladder.stage == PennyStage.UNKNOWN

    asyncio.run(run())


def test_seed_from_clearance_seed_uses_url_or_sku() -> None:
    async def run() -> None:
        db = await fresh_db()
        tracked = await seed_from_clearance_seed(
            db,
            {
                "guild_id": 7,
                "title": "Spotted vanity",
                "sku": None,
                "product_url": "https://www.homedepot.com/p/Some-Vanity/100123456",
                "store_id": "1234",
                "zip_code": "06484",
                "observed_price": 12.03,
            },
            now=T0,
        )
        assert tracked is True
        items = await list_hd_penny_items(db)
        assert len(items) == 1
        assert items[0].product_id == "100123456"
        assert items[0].stage == PennyStage.FINAL
        assert items[0].store_id == "1234"

        no_identity = await seed_from_clearance_seed(
            db, {"title": "Mystery item", "sku": None, "product_url": None}, now=T0
        )
        assert no_identity is False

    asyncio.run(run())


def test_stage_dwells_and_learned_median() -> None:
    rows = [
        ("mid", T0),
        ("mid", T0 + timedelta(days=2)),  # price moved, stage did not
        ("final", T0 + timedelta(days=10)),
        ("penny", T0 + timedelta(days=13)),
    ]
    dwells = stage_dwells_from_history(rows)
    assert dwells["mid"] == [10.0]
    assert dwells["final"] == [3.0]

    async def run() -> None:
        db = await fresh_db()
        for index, mid_days in enumerate((10, 20)):
            item, _ = await track_hd_penny_item(
                db,
                product_id=f"900{index}",
                title="Learner",
                product_url=f"https://www.homedepot.com/p/900{index}",
                price_cents=2506,
                now=T0,
            )
            await record_hd_penny_observation(
                db,
                item_id=item.item_id,
                price_cents=1003,
                now=T0 + timedelta(days=mid_days),
            )
        learned = await learned_stage_dwell_days(db, item_id=999999, stage=PennyStage.MID)
        assert learned == 15.0  # median of the two observed mid dwells

    asyncio.run(run())


def test_status_snapshot_counts_and_estimate() -> None:
    async def run() -> None:
        db = await fresh_db()
        await ensure_hd_penny_tables(db)
        await track_hd_penny_item(
            db,
            product_id="100123456",
            title="Final item",
            product_url="https://www.homedepot.com/p/100123456",
            price_cents=1003,
            reference_cents=9999,
            now=T0,
        )
        status = await load_hd_penny_status(db, now=T0 + timedelta(days=2))
        assert status.watching == 1
        assert status.stage_counts == {"final": 1}
        assert len(status.items) == 1
        entry = status.items[0]
        assert entry.stage == PennyStage.FINAL
        assert entry.days_in_stage == 2.0
        assert entry.estimate is not None
        assert entry.estimate.learned is False  # seed dwell until observed

    asyncio.run(run())
