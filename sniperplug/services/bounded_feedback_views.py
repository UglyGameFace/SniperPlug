from __future__ import annotations

import logging
from typing import Any

from sniperplug.services import deal_feedback


log = logging.getLogger("sniperplug.feedback_views")
MAX_PERSISTENT_FEEDBACK_VIEWS = 250


async def register_bounded_persistent_feedback_views(bot: Any) -> int:
    """Restore a bounded set of feedback controls after restart.

    One legacy catch-all view keeps old un-tokenized buttons functional. The
    newest token-backed targets fill the remaining bounded slots. Discord does
    not require one in-memory view for every historical deal forever.
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

    try:
        targets = list(await deal_feedback.recent_feedback_targets(db))
    except Exception:
        log.exception("Could not load recent feedback targets")
        return registered

    remaining = max(0, MAX_PERSISTENT_FEEDBACK_VIEWS - registered)
    selected = targets[:remaining]
    for token, target in selected:
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

    skipped = max(0, len(targets) - len(selected))
    if skipped:
        log.warning(
            "Persistent feedback restoration capped registered=%s skipped_old=%s cap=%s",
            registered,
            skipped,
            MAX_PERSISTENT_FEEDBACK_VIEWS,
        )
    return registered
