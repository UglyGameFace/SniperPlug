"""DM list services: OCR text handling, engine availability, storage.

Fixture-based only — no Discord connection, no live Walmart calls, no
real OCR engine (the production container has none; see
``sniperplug.services.dm_list_ocr`` for the measured gate). Storage
tests use a real in-memory SQLite connection wrapped the way the
wishlist tests wrap theirs, with the process-global schema flags reset
around every test.
"""

from __future__ import annotations

import contextlib

import aiosqlite
import pytest

import sniperplug.services.dm_lists as dm_lists
from sniperplug.services.dm_list_ocr import (
    MAX_CANDIDATE_LINES_PER_IMAGE,
    TesseractOcrEngine,
    UnavailableOcrEngine,
    extract_candidate_lines,
    get_ocr_engine,
)
from sniperplug.services.dm_lists import (
    MAX_DM_LIST_ITEMS_PER_USER,
    STATUS_CLOSEST,
    STATUS_EXACT,
    STATUS_UNCHECKED,
    STATUS_UNREADABLE,
    DmListCandidate,
    add_dm_list_items,
    list_dm_list_items,
    update_dm_list_item_check,
)

USER_ID = 111

CART_OCR_TEXT = """Cart
Cart (5 items)
Apple iPhone 11, 64GB, Green -
Unlocked
$298.00
Qty 1
Pickup tomorrow
LEGO Star Wars Millennium Falcon
Building Set
$84.95
Qty 1
Pickup tomorrow
Instant Pot Duo 7-in-1 Electric
Pressure Cooker, 6 Quart
$59.00
Qty 1
Subtotal
$615.94
Add to cart
"""

WISHLIST_OCR_TEXT = """My wishlist
Ninja AF101 Air Fryer, 4 Quart $89.99
PlayStation DualSense Wireless Controller - White
Save for later
Dyson V11 Cordless Vacuum Cleaner
Free delivery
"""


# --- candidate line extraction -------------------------------------------


def test_extract_lines_from_cart_screenshot_text() -> None:
    lines = extract_candidate_lines(CART_OCR_TEXT)
    assert lines[0] == "Apple iPhone 11, 64GB, Green"
    assert "LEGO Star Wars Millennium Falcon" in lines
    assert "Instant Pot Duo 7-in-1 Electric" in lines
    # Furniture and one-word wrap tails never become search lines.
    for noise in ("Unlocked", "Subtotal", "Qty 1", "Pickup tomorrow"):
        assert noise not in lines
    assert not any(line.startswith("$") for line in lines)
    # Longer wrap fragments ("Building Set") are searched too — plain
    # text cannot prove they are continuations — and storage dedupe by
    # pinned item ID collapses them onto the same product's one row.


def test_extract_lines_from_wishlist_screenshot_text() -> None:
    lines = extract_candidate_lines(WISHLIST_OCR_TEXT)
    assert lines == [
        "Ninja AF101 Air Fryer, 4 Quart",  # trailing price stripped
        "PlayStation DualSense Wireless Controller - White",
        "Dyson V11 Cordless Vacuum Cleaner",
    ]


def test_extract_lines_noise_only_and_empty() -> None:
    assert extract_candidate_lines("") == []
    assert extract_candidate_lines("Subtotal\n$12.99\nQty 2\nCheckout") == []
    assert extract_candidate_lines("hello") == []  # too short to be a product


def test_extract_lines_dedupes_and_caps() -> None:
    text = "\n".join(
        ["Dyson V11 Cordless Vacuum Cleaner"] * 3
        + [f"Some Product Name Number {n} Here" for n in range(20)]
    )
    lines = extract_candidate_lines(text)
    assert lines[0] == "Dyson V11 Cordless Vacuum Cleaner"
    assert len(lines) == MAX_CANDIDATE_LINES_PER_IMAGE
    assert len(lines) == len(set(lines))


# --- engines ---------------------------------------------------------------


def test_unavailable_engine_reads_nothing() -> None:
    engine = UnavailableOcrEngine()
    assert engine.available is False
    assert engine.extract_text(b"\x89PNG anything") is None


def test_tesseract_engine_unavailable_without_binary(monkeypatch) -> None:
    monkeypatch.setattr(
        "sniperplug.services.dm_list_ocr.shutil.which", lambda name: None
    )
    engine = TesseractOcrEngine()
    assert engine.available is False
    assert engine.extract_text(b"\x89PNG anything") is None


def test_default_engine_never_raises_on_garbage() -> None:
    engine = get_ocr_engine()
    result = engine.extract_text(b"this is not an image")
    assert result is None or isinstance(result, str)


# --- storage ---------------------------------------------------------------


class _FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


@contextlib.asynccontextmanager
async def _isolated_db():
    dm_lists._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    try:
        yield _FakeDatabase(conn)
    finally:
        await conn.close()
        dm_lists._SCHEMA_READY = False


def _exact(item_id: str, name: str, price_cents: int = 29800) -> DmListCandidate:
    return DmListCandidate(
        name=name,
        status=STATUS_EXACT,
        item_id=item_id,
        price_cents=price_cents,
        product_url=f"https://www.walmart.com/ip/{item_id}",
        source="link",
    )


@pytest.mark.asyncio
async def test_add_and_list_roundtrip_with_marker() -> None:
    async with _isolated_db() as db:
        outcome = await add_dm_list_items(
            db, USER_ID, [_exact("425031866", "Apple iPhone 11, 64GB, Green")]
        )
        assert len(outcome.added) == 1
        assert outcome.total == 1
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1
        row = rows[0]
        assert row.status == STATUS_EXACT
        assert row.item_id == "425031866"
        assert row.price_cents == 29800
        assert row.wishlistable is True
        # The migration marker follows the repo's one-time-table pattern.
        cursor = await db.require_conn().execute(
            "SELECT applied_at FROM sniperplug_data_migrations "
            "WHERE migration_key = '20261119_dm_list_items_table'"
        )
        assert await cursor.fetchone() is not None


@pytest.mark.asyncio
async def test_dedupe_by_item_id_then_by_name() -> None:
    async with _isolated_db() as db:
        await add_dm_list_items(db, USER_ID, [_exact("425031866", "Apple iPhone 11")])
        again = await add_dm_list_items(
            db, USER_ID, [_exact("425031866", "Apple iPhone 11 (renewed)")]
        )
        assert again.added == ()
        assert again.duplicates == 1
        # A name-only row (closest, no ID) dedupes on the normalized name.
        closest = DmListCandidate(
            name="Ninja AF101 Air Fryer", status=STATUS_CLOSEST, source="photo"
        )
        first = await add_dm_list_items(db, USER_ID, [closest])
        assert len(first.added) == 1
        second = await add_dm_list_items(
            db,
            USER_ID,
            [DmListCandidate(name="ninja  af101 air fryer", status=STATUS_CLOSEST, source="photo")],
        )
        assert second.added == ()
        assert second.duplicates == 1
        assert len(await list_dm_list_items(db, USER_ID)) == 2


@pytest.mark.asyncio
async def test_unreadable_rows_dedupe_on_source_ref_only() -> None:
    async with _isolated_db() as db:
        pic_a = DmListCandidate(
            name="", status=STATUS_UNREADABLE, source="photo", source_ref="555:0"
        )
        pic_b = DmListCandidate(
            name="", status=STATUS_UNREADABLE, source="photo", source_ref="555:1"
        )
        outcome = await add_dm_list_items(db, USER_ID, [pic_a, pic_b])
        assert len(outcome.added) == 2
        # The same message re-delivered does not double the picture.
        again = await add_dm_list_items(db, USER_ID, [pic_a])
        assert again.added == ()
        assert again.duplicates == 1
        rows = await list_dm_list_items(db, USER_ID)
        assert all(row.wishlistable is False for row in rows)


@pytest.mark.asyncio
async def test_exact_link_upgrades_a_closest_row_in_place() -> None:
    async with _isolated_db() as db:
        await add_dm_list_items(
            db,
            USER_ID,
            [
                DmListCandidate(
                    name="Apple iPhone 11",
                    status=STATUS_CLOSEST,
                    item_id="425031866",
                    source="photo",
                )
            ],
        )
        outcome = await add_dm_list_items(
            db, USER_ID, [_exact("425031866", "Apple iPhone 11, 64GB, Green")]
        )
        assert len(outcome.added) == 1
        assert outcome.total == 1
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1
        assert rows[0].status == STATUS_EXACT
        assert rows[0].price_cents == 29800
        assert rows[0].name == "Apple iPhone 11, 64GB, Green"


@pytest.mark.asyncio
async def test_list_cap_refuses_past_fifty() -> None:
    async with _isolated_db() as db:
        batch = [
            _exact(str(100000000 + n), f"Product Number {n}")
            for n in range(MAX_DM_LIST_ITEMS_PER_USER)
        ]
        outcome = await add_dm_list_items(db, USER_ID, batch)
        assert outcome.total == MAX_DM_LIST_ITEMS_PER_USER
        assert outcome.cap_reached is False
        over = await add_dm_list_items(
            db, USER_ID, [_exact("999999999", "One Product Too Many")]
        )
        assert over.added == ()
        assert over.cap_reached is True
        assert len(await list_dm_list_items(db, USER_ID)) == MAX_DM_LIST_ITEMS_PER_USER


@pytest.mark.asyncio
async def test_update_check_refreshes_a_row() -> None:
    async with _isolated_db() as db:
        outcome = await add_dm_list_items(
            db,
            USER_ID,
            [
                DmListCandidate(
                    name="Item 425031866",
                    status=STATUS_UNCHECKED,
                    item_id="425031866",
                    source="link",
                )
            ],
        )
        row = outcome.added[0]
        updated = await update_dm_list_item_check(
            db,
            row.id,
            name="Apple iPhone 11, 64GB, Green",
            price_cents=19900,
            status=STATUS_EXACT,
        )
        assert updated is not None
        assert updated.status == STATUS_EXACT
        assert updated.price_cents == 19900
        stored = (await list_dm_list_items(db, USER_ID))[0]
        assert stored.name == "Apple iPhone 11, 64GB, Green"
        assert await update_dm_list_item_check(db, 424242, price_cents=1) is None


class _NoLastrowidCursor:
    """The production libsql cursor's shape: rows, but no lastrowid."""

    def __init__(self, cursor):
        self._cursor = cursor

    async def fetchone(self):
        return await self._cursor.fetchone()

    async def fetchall(self):
        return await self._cursor.fetchall()

    def __getattr__(self, name):
        if name == "lastrowid":
            raise AttributeError("production cursor has no lastrowid")
        return getattr(self._cursor, name)


class _NoLastrowidConn:
    def __init__(self, conn):
        self._conn = conn

    async def execute(self, *args, **kwargs):
        return _NoLastrowidCursor(await self._conn.execute(*args, **kwargs))

    async def commit(self):
        await self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.mark.asyncio
async def test_add_items_with_a_production_shaped_cursor() -> None:
    """Regression (2026-11-19): the save read cursor.lastrowid,
    which the production libsql cursor does not expose — every DM
    list save crashed in production after its insert committed,
    leaving replies stuck mid-flight. The save must work against a
    cursor with no lastrowid at all."""

    dm_lists._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    try:
        db = _FakeDatabase(_NoLastrowidConn(conn))
        outcome = await add_dm_list_items(
            db, 111, [_exact("425031866", "Apple iPhone 11, 64GB, Green")]
        )
        assert len(outcome.added) == 1
        assert outcome.added[0].id > 0
        rows = await list_dm_list_items(db, 111)
        assert [row.id for row in rows] == [outcome.added[0].id]
        assert rows[0].status == STATUS_EXACT
    finally:
        await conn.close()
        dm_lists._SCHEMA_READY = False
