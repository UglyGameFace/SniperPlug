from __future__ import annotations

import contextlib
from types import SimpleNamespace

import aiosqlite
import pytest

from sniperplug.cogs.dm_list_intake import DmListView
from sniperplug.services import dm_lists
from sniperplug.services.dm_lists import (
    STATUS_EXACT,
    DmListCandidate,
    add_dm_list_items,
    clear_dm_list_items,
    list_dm_list_items,
    remove_dm_list_items,
)

USER_ID = 424242
OTHER_ID = 777


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


def _candidate(name: str, item_id: str) -> DmListCandidate:
    return DmListCandidate(
        name=name,
        status=STATUS_EXACT,
        item_id=item_id,
        price_cents=1000,
        source="link",
    )


async def _seed(db, user_id: int = USER_ID):
    await add_dm_list_items(
        db,
        user_id,
        [
            _candidate("Apple iPhone 11, 64GB, Green", "425031866"),
            _candidate("Timex Expedition Scout Watch", "123456789"),
            _candidate("Madden NYC Chelsea Boots", "987654321"),
        ],
    )
    return await list_dm_list_items(db, user_id)


# --- service ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_picked_deletes_only_those_rows():
    async with _isolated_db() as db:
        rows = await _seed(db)
        removed = await remove_dm_list_items(db, USER_ID, [rows[0].id, rows[2].id])
        assert removed == 2
        remaining = await list_dm_list_items(db, USER_ID)
        assert [row.id for row in remaining] == [rows[1].id]


@pytest.mark.asyncio
async def test_remove_cannot_touch_another_users_rows():
    async with _isolated_db() as db:
        mine = await _seed(db, USER_ID)
        await _seed(db, OTHER_ID)
        removed = await remove_dm_list_items(db, OTHER_ID, [mine[0].id])
        assert removed == 0
        assert len(await list_dm_list_items(db, USER_ID)) == 3


@pytest.mark.asyncio
async def test_remove_with_no_ids_is_zero():
    async with _isolated_db() as db:
        await _seed(db)
        assert await remove_dm_list_items(db, USER_ID, []) == 0
        assert len(await list_dm_list_items(db, USER_ID)) == 3


@pytest.mark.asyncio
async def test_clear_empties_only_that_users_list():
    async with _isolated_db() as db:
        await _seed(db, USER_ID)
        await _seed(db, OTHER_ID)
        removed = await clear_dm_list_items(db, USER_ID)
        assert removed == 3
        assert await list_dm_list_items(db, USER_ID) == ()
        assert len(await list_dm_list_items(db, OTHER_ID)) == 3
        # Clearing an already-empty list is a calm zero.
        assert await clear_dm_list_items(db, USER_ID) == 0


# --- view ------------------------------------------------------------------


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.sent: list[tuple] = []
        self.edited: list[dict] = []

    async def defer(self, **kwargs) -> None:
        self.deferred = True

    async def send_message(self, content=None, **kwargs) -> None:
        self.sent.append((content, kwargs))

    async def edit_message(self, **kwargs) -> None:
        self.edited.append(kwargs)


class _FakeInteraction:
    def __init__(self, user_id: int = USER_ID) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = _FakeResponse()
        self.original_edits: list[dict] = []

    async def edit_original_response(self, **kwargs) -> None:
        self.original_edits.append(kwargs)


def _labels(view: DmListView) -> list[str]:
    return [str(getattr(child, "label", "")) for child in view.children]


@pytest.mark.asyncio
async def test_view_has_remove_and_clear_buttons():
    async with _isolated_db() as db:
        rows = await _seed(db)
        view = DmListView(SimpleNamespace(db=db), USER_ID, rows)
        assert "Remove picked" in _labels(view)
        assert "Clear whole list" in _labels(view)


@pytest.mark.asyncio
async def test_remove_picked_flow():
    async with _isolated_db() as db:
        rows = await _seed(db)
        view = DmListView(SimpleNamespace(db=db), USER_ID, rows)
        view.selected_ids = [rows[1].id]
        interaction = _FakeInteraction()
        await view._on_remove_picked(interaction)
        remaining = await list_dm_list_items(db, USER_ID)
        assert [row.id for row in remaining] == [rows[0].id, rows[2].id]
        edit = interaction.original_edits[0]
        new_view = edit["view"]
        assert [row.id for row in new_view.rows] == [rows[0].id, rows[2].id]
        assert "Removed 1 item(s)" in new_view.check_note


@pytest.mark.asyncio
async def test_remove_picked_needs_a_pick_first():
    async with _isolated_db() as db:
        rows = await _seed(db)
        view = DmListView(SimpleNamespace(db=db), USER_ID, rows)
        interaction = _FakeInteraction()
        await view._on_remove_picked(interaction)
        assert "Pick at least one item" in interaction.response.sent[0][0]
        assert len(await list_dm_list_items(db, USER_ID)) == 3


@pytest.mark.asyncio
async def test_clear_needs_two_taps_then_empties():
    async with _isolated_db() as db:
        rows = await _seed(db)
        view = DmListView(SimpleNamespace(db=db), USER_ID, rows)
        first = _FakeInteraction()
        await view._on_clear_list(first)
        # First tap only arms the button.
        assert view.clear_button.label == "Tap again to clear your whole list"
        assert len(await list_dm_list_items(db, USER_ID)) == 3
        second = _FakeInteraction()
        await view._on_clear_list(second)
        assert await list_dm_list_items(db, USER_ID) == ()
        assert second.original_edits[0]["view"] is None
        assert "Cleared your whole list" in str(
            second.original_edits[0]["embed"].description
        )


@pytest.mark.asyncio
async def test_clear_refuses_strangers():
    async with _isolated_db() as db:
        rows = await _seed(db)
        view = DmListView(SimpleNamespace(db=db), USER_ID, rows)
        stranger = _FakeInteraction(user_id=222)
        await view._on_clear_list(stranger)
        assert "someone else's list" in stranger.response.sent[0][0]
        assert len(await list_dm_list_items(db, USER_ID)) == 3
