from __future__ import annotations

import asyncio
import ctypes
import gc
import logging
from typing import Any

from sniperplug.services.bounded_feedback_views import MAX_PERSISTENT_FEEDBACK_VIEWS


log = logging.getLogger("sniperplug.memory_hygiene")

# Runtime registrations get the same bound boot-time restoration already
# applies: after any restart only the newest MAX_PERSISTENT_FEEDBACK_VIEWS
# feedback targets are restored, so keeping more than that alive in-process
# only buys memory growth, not working buttons.
MAX_SYNCED_MESSAGE_VIEWS = MAX_PERSISTENT_FEEDBACK_VIEWS


def trim_synced_message_views(bot: Any, *, keep: int = MAX_SYNCED_MESSAGE_VIEWS) -> int:
    """Stop the oldest per-message views beyond ``keep`` newest.

    Every public deal post and movie alert sends a fresh persistent view.
    discord.py registers each one in the view store keyed by message id and,
    for timeout=None views, never removes it on its own — the store grows by
    one entry per post for the life of the process. Stopping a view invokes
    the store's own removal callback, exactly like a timeout would.

    The boot-restored persistent views are registered without a message id,
    so they never appear in the synced map this trims and are unaffected.
    Returns how many views were stopped. Any discord.py internals change
    degrades this to a no-op instead of an error.
    """

    try:
        store = getattr(getattr(bot, "_connection", None), "_view_store", None)
        synced = getattr(store, "_synced_message_views", None)
        if not isinstance(synced, dict) or len(synced) <= keep:
            return 0
        message_ids = sorted(
            (message_id for message_id in synced if isinstance(message_id, int)),
            reverse=True,
        )
        stopped = 0
        for message_id in message_ids[max(0, keep) :]:
            view = synced.get(message_id)
            if view is None:
                continue
            try:
                view.stop()
            except Exception:
                continue
            stopped += 1
        return stopped
    except Exception:
        log.exception("Could not trim synced message views")
        return 0


def release_unused_memory() -> bool:
    """Collect garbage and hand freed arenas back to the OS where supported.

    Scan passes materialize hundreds of candidate payloads; CPython keeps the
    freed arenas for reuse, so RSS sits at the largest pass's high-water mark.
    malloc_trim (glibc) returns the fully-free arenas. Returns True when the
    trim call succeeded; False on platforms/libcs without it.
    """

    gc.collect()
    try:
        libc = ctypes.CDLL("libc.so.6")
        return bool(libc.malloc_trim(0))
    except Exception:
        return False


def run_memory_hygiene(bot: Any) -> dict[str, Any]:
    """One hygiene pass: trim the view store, then release freed memory."""

    stopped = trim_synced_message_views(bot)
    trimmed = release_unused_memory()
    if stopped:
        log.info(
            "Memory hygiene stopped_views=%s malloc_trim=%s",
            stopped,
            trimmed,
        )
    return {"stopped_views": stopped, "malloc_trim": trimmed}


async def memory_hygiene_loop(
    bot: Any, *, interval_seconds: float = 600.0
) -> None:
    """Run one hygiene pass every interval for the life of the bot."""

    while True:
        await asyncio.sleep(max(60.0, float(interval_seconds)))
        try:
            run_memory_hygiene(bot)
        except Exception:
            log.exception("Memory hygiene pass failed")
