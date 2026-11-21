from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sniperplug.services.memory_hygiene import (
    MAX_SYNCED_MESSAGE_VIEWS,
    release_unused_memory,
    run_memory_hygiene,
    trim_synced_message_views,
)


class _FakeView:
    def __init__(self, store: dict, message_id: int, *, fail: bool = False) -> None:
        self._store = store
        self._message_id = message_id
        self._fail = fail
        self.stopped = False

    def stop(self) -> None:
        if self._fail:
            raise RuntimeError("boom")
        self.stopped = True
        # Mimic discord.py: stopping a view unregisters it from the store.
        self._store.pop(self._message_id, None)


def _fake_bot(message_ids: list[int], *, fail_ids: set[int] | None = None):
    fail_ids = fail_ids or set()
    synced: dict[int, _FakeView] = {}
    views = {}
    for message_id in message_ids:
        view = _FakeView(synced, message_id, fail=message_id in fail_ids)
        synced[message_id] = view
        views[message_id] = view
    store = SimpleNamespace(_synced_message_views=synced)
    bot = SimpleNamespace(_connection=SimpleNamespace(_view_store=store))
    return bot, synced, views


def test_trim_keeps_newest_and_stops_oldest():
    ids = list(range(1, MAX_SYNCED_MESSAGE_VIEWS + 26))
    bot, synced, views = _fake_bot(ids)
    stopped = trim_synced_message_views(bot)
    assert stopped == 25
    assert len(synced) == MAX_SYNCED_MESSAGE_VIEWS
    # The survivors are the newest message ids.
    assert min(synced) == 26
    assert views[1].stopped is True
    assert views[max(ids)].stopped is False


def test_trim_under_cap_is_noop():
    bot, synced, _views = _fake_bot([10, 11, 12])
    assert trim_synced_message_views(bot) == 0
    assert len(synced) == 3


def test_trim_missing_store_is_noop():
    assert trim_synced_message_views(SimpleNamespace()) == 0
    assert trim_synced_message_views(SimpleNamespace(_connection=None)) == 0


def test_trim_survives_a_failing_view():
    ids = list(range(1, MAX_SYNCED_MESSAGE_VIEWS + 4))
    bot, synced, views = _fake_bot(ids, fail_ids={1})
    stopped = trim_synced_message_views(bot)
    # The failing view stays registered; the other overflow views still stop.
    assert stopped == 2
    assert 1 in synced
    assert views[2].stopped is True


def test_release_unused_memory_returns_bool():
    assert isinstance(release_unused_memory(), bool)


def test_run_memory_hygiene_shape():
    bot, _synced, _views = _fake_bot([1, 2, 3])
    result = run_memory_hygiene(bot)
    assert result["stopped_views"] == 0
    assert isinstance(result["malloc_trim"], bool)


def test_bot_wiring_pins():
    source = Path("sniperplug/bot.py").read_text(encoding="utf-8")
    assert "max_messages=200" in source
    assert "memory_hygiene_loop(self)" in source
    assert "sniperplug-memory-hygiene" in source
