"""Saved Home Depot location: per-user ZIP + optional store.

Felix's complaint (2026-11-21): the HD hunt modal asked for ZIP/store
every time. This store follows the movie_user_location pattern but is a
sibling table — a Home Depot home store is not a movie radius.
"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from sniperplug.services.hd_user_location import (
    clear_hd_user_location,
    get_hd_user_location,
    normalize_hd_store_id,
    normalize_hd_zip,
    save_hd_user_location,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


async def fresh_db() -> FakeDatabase:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    return FakeDatabase(conn)


def test_save_and_get_roundtrip_with_store() -> None:
    async def run() -> None:
        db = await fresh_db()
        saved = await save_hd_user_location(db, user_id=111, zip_code="06484", store_id="1234")
        assert saved.zip_code == "06484"
        assert saved.store_id == "1234"
        assert saved.label == "ZIP 06484 · store 1234"
        loaded = await get_hd_user_location(db, user_id=111)
        assert loaded == saved

    asyncio.run(run())


def test_save_without_store_and_update_overwrites() -> None:
    async def run() -> None:
        db = await fresh_db()
        await save_hd_user_location(db, user_id=111, zip_code="06484")
        loaded = await get_hd_user_location(db, user_id=111)
        assert loaded is not None and loaded.store_id is None
        await save_hd_user_location(db, user_id=111, zip_code="06516", store_id="621")
        loaded = await get_hd_user_location(db, user_id=111)
        assert loaded is not None
        assert loaded.zip_code == "06516" and loaded.store_id == "621"

    asyncio.run(run())


def test_locations_are_per_user_and_clearable() -> None:
    async def run() -> None:
        db = await fresh_db()
        await save_hd_user_location(db, user_id=111, zip_code="06484")
        assert await get_hd_user_location(db, user_id=222) is None
        assert await clear_hd_user_location(db, user_id=111) is True
        assert await get_hd_user_location(db, user_id=111) is None
        assert await clear_hd_user_location(db, user_id=111) is False

    asyncio.run(run())


def test_zip_validation_and_normalization() -> None:
    assert normalize_hd_zip(" 06484-1234 ") == "06484"
    with pytest.raises(ValueError):
        normalize_hd_zip("6484")
    with pytest.raises(ValueError):
        normalize_hd_zip("abcde")
    with pytest.raises(ValueError):
        normalize_hd_zip("")


def test_store_validation() -> None:
    assert normalize_hd_store_id("") is None
    assert normalize_hd_store_id(None) is None
    assert normalize_hd_store_id(" 621 ") == "621"
    with pytest.raises(ValueError):
        normalize_hd_store_id("12")
    with pytest.raises(ValueError):
        normalize_hd_store_id("store-9")


def test_save_rejects_bad_zip_without_writing() -> None:
    async def run() -> None:
        db = await fresh_db()
        with pytest.raises(ValueError):
            await save_hd_user_location(db, user_id=111, zip_code="nope")
        assert await get_hd_user_location(db, user_id=111) is None

    asyncio.run(run())
