"""Bulk catalog persistence tests (Turso lock fix, investigation Option 1).

``CachedWalmartProvider._persist_candidates`` used to write per candidate:
``upsert_product_identity`` (execute + commit) plus, for priced candidates,
``record_price_observation`` (execute + commit) — roughly 370 serialized
lock acquisitions per ~90-product batch on the single production Turso
connection. The bulk path must write the same rows with a small bounded
number of multi-row statements and ONE commit per batch, keep per-row
failure isolation via a chunk fallback, and emit one write-telemetry line.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.cached_walmart import CachedWalmartProvider, _product_key
from sniperplug.storage.db import Database


@pytest.fixture(autouse=True)
def _no_turso_env(monkeypatch):
    for name in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "LIBSQL_URL", "LIBSQL_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)


class CountingConnection:
    """Delegating connection wrapper that counts execute/commit calls."""

    def __init__(self, conn) -> None:
        self._conn = conn
        self.execute_count = 0
        self.commit_count = 0

    async def execute(self, *args, **kwargs):
        self.execute_count += 1
        return await self._conn.execute(*args, **kwargs)

    async def commit(self):
        self.commit_count += 1
        return await self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


async def _make_db(tmp_path, name: str) -> Database:
    db = Database(str(tmp_path / name))
    await db.connect()
    await db.init()
    return db


def _candidate(index: int, **overrides) -> SourceCandidate:
    values = dict(
        source_key="catalog",
        retailer="Walmart",
        title=f"Bulk Test Product {index}",
        product_url=f"https://www.walmart.com/ip/{5000 + index}",
        current_price=49.99,
        typical_price=99.99,
        image_url=f"https://images.example/{index}.png",
        product_id=str(5000 + index),
        sku=f"SKU-{index}",
        upc=f"UPC-{index}",
        model=f"MODEL-{index}",
        variant_attributes={"brand": "Acme", "trustedReferenceSource": "test-source"},
    )
    values.update(overrides)
    return SourceCandidate(**values)


async def _legacy_persist(db: Database, candidates) -> list[str]:
    """The pre-fix per-candidate loop, reproduced against the public helpers.

    This encodes the old observable behavior independently of the provider so
    the equivalence test compares the new bulk path against it directly.
    """

    errors: list[str] = []
    for candidate in candidates:
        product_key = _product_key(candidate)
        if not product_key:
            continue
        try:
            await db.upsert_product_identity(
                retailer="Walmart",
                product_key=product_key,
                product_id=candidate.product_id,
                sku=candidate.sku,
                upc=candidate.upc,
                model=candidate.model,
                title=candidate.title,
                brand=(candidate.variant_attributes or {}).get("brand"),
                canonical_url=candidate.product_url,
                image_url=candidate.image_url,
                last_seen_price=candidate.current_price,
            )
        except Exception as exc:
            errors.append(f"identity write failed for {product_key}: {exc}")

        if candidate.current_price is not None and candidate.current_price > 0:
            try:
                await db.record_price_observation(
                    retailer="Walmart",
                    product_key=product_key,
                    current_price=float(candidate.current_price),
                    product_id=candidate.product_id,
                    sku=candidate.sku,
                    upc=candidate.upc,
                    title=candidate.title,
                    product_url=candidate.product_url,
                    reference_price=candidate.typical_price,
                    reference_source=(candidate.variant_attributes or {}).get("trustedReferenceSource")
                    or (candidate.variant_attributes or {}).get("reference_price_source"),
                    source_key=candidate.source_key,
                )
            except Exception as exc:
                errors.append(f"price observation failed for {product_key}: {exc}")
    return errors


def test_catalog_persist_uses_bounded_statements_and_one_commit(tmp_path) -> None:
    async def run() -> None:
        db = await _make_db(tmp_path, "bulk.sqlite3")
        counting = CountingConnection(db.conn)
        db.conn = counting
        provider = CachedWalmartProvider(db)
        candidates = tuple(_candidate(i) for i in range(90))

        errors = await provider._persist_candidates(candidates)

        assert errors == []
        # Old path: (90 identities + 90 observations) x (execute + commit)
        # = 360 lock acquisitions. Bulk path: 4 + 4 chunk statements, 1 commit.
        assert counting.commit_count == 1
        assert counting.execute_count <= 10
        cursor = await db.conn.execute("SELECT COUNT(*) FROM product_identity")
        assert (await cursor.fetchone())[0] == 90
        cursor = await db.conn.execute("SELECT COUNT(*) FROM price_observations")
        assert (await cursor.fetchone())[0] == 90
        await db.close()

    asyncio.run(run())


def test_catalog_persist_isolates_one_malformed_candidate(tmp_path) -> None:
    async def run() -> None:
        db = await _make_db(tmp_path, "isolation.sqlite3")
        counting = CountingConnection(db.conn)
        db.conn = counting
        provider = CachedWalmartProvider(db)
        candidates = [_candidate(i) for i in range(30)]
        bad = _candidate(10, title={"unbindable": "mapping"})
        candidates[10] = bad
        bad_key = _product_key(bad)

        errors = await provider._persist_candidates(tuple(candidates))

        assert len(errors) == 2
        assert any(e.startswith(f"identity write failed for {bad_key}:") for e in errors)
        assert any(e.startswith(f"price observation failed for {bad_key}:") for e in errors)
        assert counting.commit_count == 1
        cursor = await db.conn.execute("SELECT COUNT(*) FROM product_identity")
        assert (await cursor.fetchone())[0] == 29
        cursor = await db.conn.execute("SELECT COUNT(*) FROM price_observations")
        assert (await cursor.fetchone())[0] == 29
        cursor = await db.conn.execute(
            "SELECT COUNT(*) FROM product_identity WHERE product_key = ?", (bad_key,)
        )
        assert (await cursor.fetchone())[0] == 0
        await db.close()

    asyncio.run(run())


def test_catalog_persist_writes_the_same_rows_as_the_legacy_path(tmp_path) -> None:
    async def identity_rows(db: Database):
        cursor = await db.conn.execute(
            """
            SELECT retailer, product_key, product_id, sku, upc, model, title,
                   brand, canonical_url, image_url, last_seen_price,
                   last_seen_at IS NULL
            FROM product_identity
            ORDER BY retailer, product_key
            """
        )
        return [tuple(row) for row in await cursor.fetchall()]

    async def observation_rows(db: Database):
        cursor = await db.conn.execute(
            """
            SELECT retailer, product_key, product_id, sku, upc, store_id,
                   zip_code, title, product_url, current_price, reference_price,
                   reference_source, source_key
            FROM price_observations
            ORDER BY product_key, title, current_price
            """
        )
        return [tuple(row) for row in await cursor.fetchall()]

    async def run() -> None:
        candidates = (
            _candidate(0),
            _candidate(1),
            _candidate(2),
            _candidate(3, current_price=None, typical_price=None),
            _candidate(4, current_price=0.0),
            _candidate(5, product_id=None, sku=None, upc="012345678905"),
            _candidate(6, variant_attributes={}, current_price=10.0, typical_price=25.0),
            _candidate(0, title="Bulk Test Product 0 (repriced)", current_price=39.99),
            _candidate(7, product_id=None, selected_offer_id=None, upc=None, sku=None),
        )

        legacy_db = await _make_db(tmp_path, "legacy.sqlite3")
        legacy_errors = await _legacy_persist(legacy_db, candidates)

        bulk_db = await _make_db(tmp_path, "bulk.sqlite3")
        provider = CachedWalmartProvider(bulk_db)
        bulk_errors = await provider._persist_candidates(candidates)

        assert legacy_errors == []
        assert bulk_errors == []
        assert await identity_rows(bulk_db) == await identity_rows(legacy_db)
        assert await observation_rows(bulk_db) == await observation_rows(legacy_db)
        await legacy_db.close()
        await bulk_db.close()

    asyncio.run(run())


def test_catalog_persist_emits_write_telemetry(tmp_path, caplog) -> None:
    async def run() -> None:
        db = await _make_db(tmp_path, "telemetry.sqlite3")
        provider = CachedWalmartProvider(db)
        errors = await provider._persist_candidates(tuple(_candidate(i) for i in range(30)))
        assert errors == []
        await db.close()

    with caplog.at_level(logging.INFO, logger="sniperplug.autoscan.write_telemetry"):
        asyncio.run(run())

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "sniperplug.autoscan.write_telemetry"
    ]
    assert any(
        "Walmart catalog Turso write telemetry" in message
        and "candidates=30" in message
        and "identity_rows=30" in message
        and "observation_rows=30" in message
        and "commits=1" in message
        for message in messages
    )
