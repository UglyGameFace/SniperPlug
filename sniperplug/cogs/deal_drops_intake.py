"""Phase D3: #deal-drops auto-intake.

Any ``walmart.com/ip`` link or bare Walmart item ID posted in the
designated channel is auto-verified through ``verify_single_exact_item`` —
the same exact path ``/walmart check`` uses. No new verification logic, no
gate/threshold changes.

Guardrails (all cheap, all in this module):

* Only the channel named by ``SNIPERPLUG_DEAL_DROPS_CHANNEL_ID``; an
  unset/empty channel ID disables intake entirely. ``on_message`` also
  returns immediately when the message-content intent is off
  (``ENABLE_MESSAGE_CONTENT_INTENT`` / the Discord developer-portal
  toggle), so the listener is inert until Felix enables both.
* Only non-bot humans. The movie-ticket feedback cog's ``on_message``
  listener is unaffected: it only handles the bot's own movie-alert
  embeds, while this one only handles non-bot authors in the drops
  channel, so the two listeners never act on the same message.
* Dedupe: the same item ID posted again within
  ``SNIPERPLUG_DEAL_DROPS_DEDUPE_MINUTES`` (default 15) is not
  re-verified. This dedupe is needed here because the exact queue does
  NOT make a re-post a no-op: ``verify_single_exact_item`` always runs a
  live fetch (``_fetch_exact_candidate``) and the bulk upsert bumps
  ``discovered_count`` / rewrites ``last_seen_at`` on conflict
  (``walmart_exact_verification_queue_bulk.py``, ``ON CONFLICT(item_id)
  DO UPDATE``), so without this in-memory guard a re-post would cost
  another live verification.
* Rate limit: at most ``SNIPERPLUG_DEAL_DROPS_MAX_PER_MINUTE``
  (default 5) verifications per minute in the channel; extras are
  skipped with a one-line note, not retried.
* At most ``MAX_ITEMS_PER_MESSAGE`` (5) item IDs per message.

Reply shape: Discord cannot send an ephemeral reply to a plain message,
so the cog posts one short plain-text ``message.reply`` (no author ping)
with one line per item — a verified markdown only when the
classification says so, a page-observed price labelled as exactly that,
never a % claim. No embeds, no new commands.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass

import discord
from discord.ext import commands

from sniperplug.providers.base import ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.walmart_exact_verification_queue import (
    MANUAL_INTAKE_MIN_DISCOUNT,
    VERIFIED_PAGE_PRICE_STATUS,
    page_observed_price,
    verify_single_exact_item,
)

log = logging.getLogger("sniperplug")

# One walmart.com/ip URL anywhere in a token. Deliberately looser than the
# queue module's pattern (which validates one clean value): free message
# text may trail the ID with punctuation, a query string, or a quote, so
# the ID only has to be followed by a non-alphanumeric (or end of token).
_WALMART_IP_URL_RE = re.compile(
    r"walmart\.com/ip/(?:[^/?#\s]+/)?(\d{1,20})(?![0-9A-Za-z])",
    re.IGNORECASE,
)

# Bare item IDs must be standalone tokens (after stripping chat
# punctuation) so random numbers — quantities, years, prices — never
# trigger a verification. 7 digits is the floor: real Walmart item IDs
# are 9 digits, and anything shorter in chat is almost never an item ID.
_BARE_ITEM_ID_MIN_DIGITS = 7
_BARE_ITEM_ID_MAX_DIGITS = 20
_TOKEN_STRIP_CHARS = "\"'`<([{#.,!?:;)>}]"

# Cap per message: a paste of many links verifies the first few and notes
# the rest instead of hammering the provider.
MAX_ITEMS_PER_MESSAGE = 5

_TITLE_MAX_CHARS = 80


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class DealDropsIntakeConfig:
    """D3 auto-intake settings. Unset channel ID disables intake."""

    enabled: bool = True
    channel_id: int = 0
    max_per_minute: int = 5
    dedupe_minutes: int = 15

    @classmethod
    def from_env(cls) -> DealDropsIntakeConfig:
        return cls(
            enabled=_env_bool("SNIPERPLUG_DEAL_DROPS_ENABLED", True),
            channel_id=_env_int("SNIPERPLUG_DEAL_DROPS_CHANNEL_ID", 0),
            max_per_minute=max(1, _env_int("SNIPERPLUG_DEAL_DROPS_MAX_PER_MINUTE", 5)),
            dedupe_minutes=max(
                0, _env_int("SNIPERPLUG_DEAL_DROPS_DEDUPE_MINUTES", 15)
            ),
        )


def extract_walmart_item_ids(content: str) -> list[str]:
    """Extract Walmart item IDs from free message text, in first-seen order.

    Finds ``walmart.com/ip/.../<id>`` links anywhere in a token and
    standalone digit-only tokens of 7-20 digits. Non-Walmart links are
    ignored, as are short numbers (quantities, years, prices). Duplicate
    IDs in one message collapse to a single entry.
    """

    found: list[str] = []
    seen: set[str] = set()
    for token in (content or "").split():
        item_id: str | None = None
        match = _WALMART_IP_URL_RE.search(token)
        if match is not None:
            item_id = match.group(1)
        else:
            clean = token.strip(_TOKEN_STRIP_CHARS)
            if (
                clean.isdigit()
                and _BARE_ITEM_ID_MIN_DIGITS <= len(clean) <= _BARE_ITEM_ID_MAX_DIGITS
            ):
                item_id = clean
        if item_id is not None and item_id not in seen:
            seen.add(item_id)
            found.append(item_id)
    return found


def _money(value: object) -> str:
    try:
        return f"${float(value):,.2f}" if value is not None else "N/A"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "N/A"


def _short_title(title: str, fallback: str) -> str:
    text = (title or "").strip() or fallback
    if len(text) > _TITLE_MAX_CHARS:
        return text[: _TITLE_MAX_CHARS - 1].rstrip() + "…"
    return text


class DealDropsIntakeCog(commands.Cog):
    """Auto-verify Walmart links / item IDs posted in the drops channel."""

    def __init__(
        self, bot: commands.Bot, config: DealDropsIntakeConfig | None = None
    ) -> None:
        self.bot = bot
        self.config = config or DealDropsIntakeConfig.from_env()
        # item_id -> monotonic timestamp of the last verification start.
        self._verified_at: dict[str, float] = {}
        # Monotonic timestamps of verification starts in the last minute.
        self._verify_starts: list[float] = []

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Verify Walmart drops posted in the designated channel."""

        if not self.config.enabled or not self.config.channel_id:
            return
        intents = getattr(self.bot, "intents", None)
        if intents is not None and not getattr(
            intents, "message_content", False
        ):
            # Without the message-content intent Discord delivers empty
            # message content anyway; stay explicitly inert instead of
            # silently doing nothing on every message.
            return
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return
        bot_user = getattr(self.bot, "user", None)
        if (
            bot_user is not None
            and getattr(author, "id", None) == getattr(bot_user, "id", None)
        ):
            return
        channel = getattr(message, "channel", None)
        if channel is None or getattr(channel, "id", None) != self.config.channel_id:
            return
        item_ids = extract_walmart_item_ids(getattr(message, "content", "") or "")
        if not item_ids:
            return
        await self._handle_items(message, item_ids)

    async def _handle_items(
        self, message: discord.Message, item_ids: list[str]
    ) -> None:
        now = time.monotonic()
        dedupe_seconds = self.config.dedupe_minutes * 60.0
        fresh: list[str] = []
        duplicates: list[str] = []
        for item_id in item_ids[:MAX_ITEMS_PER_MESSAGE]:
            last = self._verified_at.get(item_id)
            if last is not None and now - last < dedupe_seconds:
                duplicates.append(item_id)
            else:
                fresh.append(item_id)
        overflow = item_ids[MAX_ITEMS_PER_MESSAGE:]

        self._verify_starts = [
            started for started in self._verify_starts if now - started < 60.0
        ]
        allowed = max(0, self.config.max_per_minute - len(self._verify_starts))
        to_verify, rate_limited = fresh[:allowed], fresh[allowed:]

        lines: list[str] = []
        if to_verify:
            provider = provider_registry.get("walmart")
            if provider is None:
                await self._reply(
                    message, "Walmart search is not connected yet."
                )
                return
            try:
                health = await provider.healthcheck()
            except Exception as error:  # noqa: BLE001 - report, don't crash the listener.
                log.exception("Deal-drops intake healthcheck failed: %s", error)
                await self._reply(
                    message, "Deal search is not ready yet. Try /walmart check later."
                )
                return
            if health.status != ProviderStatus.READY:
                await self._reply(
                    message,
                    "Deal search is not ready yet. Staff needs to finish "
                    "the Walmart connection first.",
                )
                return
            db = getattr(self.bot, "db", None)
            for item_id in to_verify:
                started = time.monotonic()
                self._verified_at[item_id] = started
                self._verify_starts.append(started)
                try:
                    result = await verify_single_exact_item(
                        db,
                        provider,
                        item_id,
                        min_discount=MANUAL_INTAKE_MIN_DISCOUNT,
                    )
                except Exception as error:  # noqa: BLE001 - one bad item must not drop the rest.
                    log.exception(
                        "Deal-drops intake failed for item %s: %s", item_id, error
                    )
                    lines.append(
                        f"❌ item {item_id}: direct check hit an error: `{error}`"
                    )
                    continue
                lines.append(self._result_line(result))
            log.info(
                "Deal-drops intake verified channel_id=%s items=%s",
                self.config.channel_id,
                ",".join(to_verify),
            )
        for item_id in duplicates:
            lines.append(
                f"⏭️ item {item_id}: already checked recently, skipped."
            )
        if rate_limited:
            lines.append(
                f"⏭️ {len(rate_limited)} item(s) skipped "
                f"(max {self.config.max_per_minute}/min in this channel — "
                "use /walmart check)."
            )
        if overflow:
            lines.append(
                f"⏭️ {len(overflow)} item(s) skipped "
                f"(max {MAX_ITEMS_PER_MESSAGE} per message — use /walmart check)."
            )
        if lines:
            await self._reply(message, "\n".join(lines))

    @staticmethod
    def _result_line(result: object) -> str:
        item_id = getattr(result, "item_id", "?")
        candidate = getattr(result, "candidate", None)
        status = getattr(result, "status", "retry")
        classification = getattr(result, "classification", None)
        if candidate is None:
            error = getattr(result, "error", "")
            line = f"❌ item {item_id}: could not verify (`{status}`"
            if error:
                line += f" — {error}"
            line += ")"
            if getattr(result, "enqueued", False):
                line += " • queued for the normal exact worker to retry."
            else:
                line += " • not queued (no database available)."
            return line
        title = _short_title(
            str(getattr(candidate, "title", "") or ""), f"item {item_id}"
        )
        current = getattr(candidate, "api_current_price", None) or getattr(
            candidate, "current_price", None
        )
        reference = getattr(candidate, "api_reference_price", None) or getattr(
            candidate, "typical_price", None
        )
        if classification == VERIFIED_PAGE_PRICE_STATUS:
            # A page-observed price has no API price: label it as exactly
            # that, never a markdown.
            page_price = page_observed_price(candidate)
            if page_price is not None:
                return (
                    f"🔎 {title} — page-observed {_money(page_price)} "
                    f"(no API price, not a proven markdown) • item {item_id}"
                )
        if classification == "verified_markdown":
            line = f"✅ {title} — {_money(current)}"
            if reference:
                line += f" (was {_money(reference)})"
            return line + f" • verified markdown • item {item_id}"
        return (
            f"🔎 {title} — {_money(current)} • "
            f"exact check: {classification or status} • item {item_id}"
        )

    @staticmethod
    async def _reply(message: discord.Message, content: str) -> None:
        try:
            await message.reply(content, mention_author=False)
        except (discord.NotFound, discord.HTTPException, discord.Forbidden):
            log.exception("Deal-drops intake could not reply in-channel")
