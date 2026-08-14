from __future__ import annotations

import logging
from typing import Any

from sniperplug.services import deal_feedback


log = logging.getLogger("sniperplug.feedback_views")
MAX_PERSISTENT_FEEDBACK_VIEWS = 250


async def register_bounded_persistent_feedback_views(bot: Any) -> int:
    """Restore only the feedback controls that can actually be registered.

    One legacy catch-all view keeps old un-tokenized buttons functional. The
    remaining slots are filled from the newest token-backed targets. Do not
    fetch hundreds of additional targets just to discard them in memory; that
    unnecessary startup read is especially costly on remote Turso.
    """

    registered = 0
    try:
        bot.add_view(deal_feedback.DealFeedbackView(None, persistent=True))
        registered += 1
    except Exception:
        log.exception("Could not register legacy feedback catch-all view")

    db = getattr(bot, "db", None)
    if db is None:
        return registered

    remaining = max(0, MAX_PERSISTENT_FEEDBACK_VIEWS - registered)
    if remaining <= 0:
        return registered

    try:
        targets = list(await deal_feedback.recent_feedback_targets(db, limit=remaining))
    except Exception:
        log.exception("Could not load recent feedback targets")
        return registered

    for token, target in targets:
        try:
            bot.add_view(
                deal_feedback.DealFeedbackView(
                    target,
                    token=token,
                    persistent=True,
                )
            )
            registered += 1
        except Exception:
            log.exception("Could not restore feedback view token=%s", token)

    log.info(
        "Persistent feedback restoration bounded registered=%s query_limit=%s cap=%s",
        registered,
        remaining,
        MAX_PERSISTENT_FEEDBACK_VIEWS,
    )
    return registered
