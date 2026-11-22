"""DM list intake: pictures and link piles in DMs become a saved list.

Felix's ask (2026-11-19): users DM the bot pictures of items,
screenshots of wishlists/carts, or a pile of cart links (his
girlfriend sends links, never a real wishlist). The bot finds those
exact items and replies with a tappable menu; from the menu, items go
onto the user's existing wishlist. This cog is that intake. The list
itself lives in ``sniperplug.services.dm_lists``; picture reading
lives behind the engine interface in
``sniperplug.services.dm_list_ocr`` (see that module for why the
production container currently reports picture reading as not
switched on — the links half of this feature is complete without it).

Guardrails (all cheap, all in this module):

* DMs only: guild messages are ignored (the #deal-drops channel has
  its own intake), group DMs are ignored, other bots are ignored.
  DM content and attachments are delivered without the privileged
  message-content intent, so — unlike the channel watcher — this
  listener does not gate on that intent.
* Links reuse the channel watcher's extraction
  (``extract_walmart_item_ids``) and the exact verifier
  (``verify_single_exact_item``) — no new verification logic, no gate
  or threshold changes.
* Screenshot lines are searched through the provider's normal search
  and then pinned through the same verifier. A row is "exact match"
  only when the verifier pinned it; a search candidate the verifier
  could not pin is a "closest match" and carries no price, because no
  price was proven for it.
* Work per message is bounded: at most ``MAX_VERIFICATIONS_PER_MESSAGE``
  live verifications (links first — they are the exact ones — then
  screenshot candidates), at most ``MAX_ATTACHMENTS_PER_MESSAGE``
  pictures, each at most ~10 MB. Rows past the verification budget are
  still saved in their unverified state, so "Check prices now" can
  finish the job later; the reply says when the budget ran out.
* Text-only DMs get one short helpful reply, throttled per user so a
  burst of messages earns one reply, not five.
* Picture reading itself runs in the standalone SniperPlug-Reader
  app (the OCR engine provably cannot fit this bot's container; see
  ``sniperplug.services.dm_list_ocr``). When the reader's heartbeat
  is fresh, a picture is handed to it by URL — never downloaded
  here — and the intake replies at once with the link results plus
  a "Reading your picture…" note; a background task settles the
  picture when the read returns (same search → verify pipeline,
  same budgets), writes its rows, and edits the reply into the
  final list. A read that times out, fails, or loses the reader
  mid-flight settles exactly like the no-engine path: an unreadable
  row and the plain "isn't switched on yet" copy. When the
  heartbeat is stale or absent, intake behavior is the pre-reader
  behavior, unchanged — the heartbeat is the switch; there is no
  env flag for it on the bot.

The reopen door is ``/menu`` → My deals → **My list**
(``open_my_list``, delegated by the unified menu — no new slash
command; the surface stays at the consolidated 7 top-levels).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, replace
from typing import Iterable

import discord
from discord.ext import commands

from sniperplug.cogs.deal_drops_intake import extract_walmart_item_ids
from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.dm_deal_alerts import (
    get_dm_deal_alert_preference,
    save_dm_deal_alert_preference,
)
from sniperplug.services.dm_list_ocr import (
    OcrEngine,
    RemoteOcrEngine,
    extract_candidate_lines,
    get_ocr_engine,
)
from sniperplug.services.dm_lists import (
    MAX_DM_LIST_ITEMS_PER_USER,
    STATUS_CLOSEST,
    STATUS_EXACT,
    STATUS_UNCHECKED,
    STATUS_UNREADABLE,
    DmListAddOutcome,
    DmListCandidate,
    DmListRow,
    add_dm_list_items,
    clear_dm_list_items,
    list_dm_list_items,
    remove_dm_list_items,
    update_dm_list_item_check,
)
from sniperplug.services.dm_ocr_jobs import (
    JOB_DONE,
    consume_dm_ocr_job,
    list_orphaned_finished_jobs,
    list_unsettled_picture_reads,
    mark_pending_picture_read_settled,
    record_pending_picture_read,
)
from sniperplug.services.dm_product_interests import normalize_match_text
from sniperplug.services.error_logging import make_error_id, record_exception_event
from sniperplug.services.user_wishlist import (
    MAX_WISHLIST_ITEMS_PER_USER,
    add_user_wishlist_item,
)
from sniperplug.services.walmart_exact_verification_queue import (
    MANUAL_INTAKE_MIN_DISCOUNT,
    VERIFIED_PAGE_PRICE_STATUS,
    page_observed_price,
    parse_walmart_item_id,
    verify_single_exact_item,
)

log = logging.getLogger("sniperplug")

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 4
MAX_VERIFICATIONS_PER_MESSAGE = 10
MAX_SEARCHES_PER_MESSAGE = 8
MAX_RECHECKS_PER_TAP = 10
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
_IMAGE_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
HELP_REPLY_COOLDOWN_SECONDS = 600.0
_SELECT_LIMIT = 25
_NAME_MAX_CHARS = 120

HELP_TEXT = (
    "Send me a picture of an item, a screenshot of a cart or wishlist, "
    "or paste Walmart links — I'll find the items and keep them on your "
    "list. Open your list anytime from /menu → My deals → My list."
)
HELP_TEXT_WRONG_FILE = (
    "I can only read pictures (png, jpg, webp) and Walmart links. "
    "Send one of those and I'll add the items to your list."
)

# Reply notes shared by the immediate path and the background task
# that settles a remote picture read, so both say the same words.
TOO_BIG_NOTE = (
    "One picture was too big for me to read (over 10 MB) — "
    "it isn't on your list."
)
OCR_OFF_NOTE = (
    "Picture reading isn't switched on yet, so I saved that "
    "picture to your list but couldn't read it. Links work "
    "today — paste them and I'll check every one."
)
VERIFY_BUDGET_NOTE = (
    f"I checked the first {MAX_VERIFICATIONS_PER_MESSAGE} "
    "items live — the rest are saved, and **Check prices "
    "now** will check them."
)
LIST_FULL_NOTE = (
    f"Your list is full ({MAX_DM_LIST_ITEMS_PER_USER} items), "
    "so some couldn't be added."
)
SETTLE_FAILURE_NOTE = (
    "Something went wrong while reading those pictures, so I "
    "couldn't finish. Your list is under /menu → My deals → "
    "My list — send the pictures again and I'll read them then."
)

# Crash recovery for pending picture reads. A settle runs in a
# background task; when the process died mid-read on 2026-11-21 the
# task died with it and the reply sat on "Reading your picture…"
# forever. Every deferred read is therefore recorded in
# ``dm_list_pending_reads`` before its task starts, and a recovery
# pass re-runs the settle for any record still unsettled past the
# age floor — past it, no live waiter can still own the reply (the
# remote waiter's own timeout is 120 s), so re-running cannot race
# the original task; a per-process set of live record ids is the
# second guard. The pass runs once shortly after boot and then on
# an interval, and it also settles orphaned finished reader jobs
# (a completed read whose waiter died before consuming it) from
# their stored text, so the items still land on the user's list.
PENDING_READ_MIN_AGE_SECONDS = 150.0
PENDING_READ_RECOVERY_INTERVAL_SECONDS = 120.0
PENDING_READ_RECOVERY_INITIAL_DELAY_SECONDS = 20.0
PENDING_READ_RECOVERY_BATCH = 3
ORPHANED_JOB_MIN_AGE_SECONDS = 600.0


def reading_picture_note(count: int) -> str:
    """The interim note while the reader app works on a picture."""

    noun = "pictures" if count > 1 else "picture"
    return f"📖 Reading your {noun}… I'll update this message with what I find."


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class DmListIntakeConfig:
    """DM list intake settings. One kill switch; everything else is code."""

    enabled: bool = True

    @classmethod
    def from_env(cls) -> "DmListIntakeConfig":
        return cls(enabled=_env_bool("SNIPERPLUG_DM_LIST_ENABLED", True))


def _money_cents(cents: int | None) -> str:
    if cents is None:
        return ""
    if cents % 100 == 0:
        return f"${cents // 100:,}"
    return f"${cents / 100:,.2f}"


def _short_name(name: str) -> str:
    text = " ".join(str(name or "").split())
    if len(text) > _NAME_MAX_CHARS:
        return text[: _NAME_MAX_CHARS - 1].rstrip() + "…"
    return text


def _linked_name(row: DmListRow) -> str:
    """The row's name as a tappable link when its product page is known.

    Felix (2026-11-19): the list showed names and prices but no way
    to open the item — every row that carries a product URL now
    links straight to it (Walmart page, which opens the app or the
    browser). Rows without a URL keep the plain bold name.
    """

    name = _short_name(row.name)
    url = str(row.product_url or "").strip()
    if not url.startswith(("https://", "http://")):
        return f"**{name}**"
    # Brackets in the visible text would break the markdown link.
    safe = name.replace("[", "").replace("]", "")
    return f"[**{safe}**]({url})"


def _sale_segment(row: DmListRow) -> str:
    """The deal truth for a priced row, in Felix's words (2026-11-19):
    show the item, but say plainly when it isn't on sale."""

    price = row.price_cents
    reference = row.reference_price_cents
    if price and reference and reference > price:
        percent = round((1 - price / reference) * 100)
        if percent >= 1:
            return f" · was {_money_cents(reference)} · {percent}% off"
    return " · not on sale right now"


def dm_list_row_line(
    row: DmListRow, *, page_price: bool = False, already_on_list: bool = False
) -> str:
    """One embed line for a list row, with its honest status."""

    suffix = " · already on your list" if already_on_list else ""
    if row.status == STATUS_EXACT:
        line = f"✅ {_linked_name(row)}"
        price = _money_cents(row.price_cents)
        if price:
            line += f" — {price}"
            if page_price:
                line += " (page price)"
            line += _sale_segment(row)
        return line + " · exact match" + suffix
    if row.status == STATUS_CLOSEST:
        return (
            f"🔎 {_linked_name(row)} · closest match — "
            "price not checked yet" + suffix
        )
    if row.status == STATUS_UNCHECKED:
        return (
            f"🔎 {_linked_name(row)} · couldn't check it just "
            "now — tap **Check prices now**" + suffix
        )
    return "📷 A picture I couldn't read" + suffix


def build_dm_list_embed(
    rows: tuple[DmListRow, ...] | list[DmListRow],
    *,
    title: str,
    notes: Iterable[str] = (),
    page_price_item_ids: frozenset[str] = frozenset(),
    duplicate_ids: frozenset[int] = frozenset(),
) -> discord.Embed:
    """The list as an embed: one line per row, then the plain notes."""

    lines = [
        dm_list_row_line(
            row,
            page_price=bool(row.item_id and row.item_id in page_price_item_ids),
            already_on_list=row.id in duplicate_ids,
        )
        for row in rows
    ]
    for note in notes:
        if note:
            lines.append("")
            lines.append(note)
    return discord.Embed(
        title=title,
        description="\n".join(lines)[:4096],
        color=discord.Color(0x9B59B6),
    )


def _is_image_attachment(attachment: object) -> bool:
    content_type = str(getattr(attachment, "content_type", "") or "")
    if content_type in _IMAGE_CONTENT_TYPES:
        return True
    filename = str(getattr(attachment, "filename", "") or "").lower()
    return filename.endswith(_IMAGE_EXTENSIONS)


# A picture line may only pin a search hit whose title actually
# matches the line. Walmart search returns loosely related products,
# and taking the first hit sight-unseen pinned wrong flea treatments
# for badge lines on a real Amazon cart screenshot (2026-11-19).
# The bar is token containment: a share of the line's meaningful
# tokens must appear in the hit's title (product titles run longer
# than the lines read off a cart, so the line's tokens are the
# denominator). Measured on Felix's real screenshots (2026-11-19):
# unrelated UI lines score 0.00–0.33 against the products they used
# to pin, a garbled-but-real title fragment scores 0.50 against its
# true product (0.25 against the wrong brand), and clean cart lines
# score 0.78–1.00. 0.4 sits between the junk ceiling and the real
# floor.
_TITLE_MATCH_MIN = 0.4
_MATCH_STOP_TOKENS = frozenset(
    {"the", "a", "an", "for", "and", "or", "of", "with", "in", "on", "to", "by"}
)


def _match_tokens(text: object) -> frozenset[str]:
    return frozenset(
        token
        for token in normalize_match_text(text).split()
        if len(token) >= 3 and token not in _MATCH_STOP_TOKENS
    )


def _title_match_score(line: str, title: object) -> float:
    line_tokens = _match_tokens(line)
    if not line_tokens:
        return 0.0
    shared = line_tokens & _match_tokens(title)
    return len(shared) / len(line_tokens)


def _best_title_match(line: str, candidates: Iterable[object]) -> object | None:
    """The hit that matches the line best, or None when none does."""

    best: object | None = None
    best_score = 0.0
    for candidate in candidates:
        score = _title_match_score(line, getattr(candidate, "title", ""))
        if score > best_score:
            best = candidate
            best_score = score
    if best is None or best_score < _TITLE_MATCH_MIN:
        return None
    return best


def _candidate_item_id(candidate: object) -> str | None:
    product_id = str(getattr(candidate, "product_id", "") or "").strip()
    if product_id.isdigit():
        return product_id
    return parse_walmart_item_id(
        getattr(candidate, "direct_product_url", None)
        or getattr(candidate, "product_url", None)
    )


def _verified_price_cents(result: object) -> tuple[int | None, bool]:
    """(price cents, is page-observed) proven by an exact verification."""

    candidate = getattr(result, "candidate", None)
    if candidate is None:
        return None, False
    if getattr(result, "classification", None) == VERIFIED_PAGE_PRICE_STATUS:
        page_price = page_observed_price(candidate)
        if page_price is not None:
            return int(round(float(page_price) * 100)), True
    price = getattr(candidate, "api_current_price", None) or getattr(
        candidate, "current_price", None
    )
    if price is None:
        return None, False
    try:
        return int(round(float(price) * 100)), False
    except (TypeError, ValueError):
        return None, False


# Classifications where the verifier's candidate carries a TRUSTED
# reference price (its classifier only computes a discount from a
# trusted reference). Anything else — no reference, a page-observed
# price — proves no discount, and the list must say so.
_REFERENCE_CLASSIFICATIONS = frozenset(
    {"verified_markdown", "verified_under_threshold", "verified_reference"}
)


def _verified_reference_cents(result: object) -> int | None:
    """The was-price in cents when the verification proved one."""

    if getattr(result, "classification", None) not in _REFERENCE_CLASSIFICATIONS:
        return None
    candidate = getattr(result, "candidate", None)
    raw = getattr(candidate, "api_reference_price", None) if candidate else None
    if raw is None:
        return None
    try:
        cents = int(round(float(raw) * 100))
    except (TypeError, ValueError):
        return None
    return cents if cents > 0 else None


class _Budget:
    """One shared verification budget per intake message."""

    def __init__(self, total: int) -> None:
        self.left = total
        self.spent_any = False
        self.ran_out = False

    def spend(self) -> bool:
        if self.left <= 0:
            self.ran_out = True
            return False
        self.left -= 1
        self.spent_any = True
        return True


@dataclass
class _IntakeNotes:
    """Reply notes, each said at most once per intake."""

    lines: list[str] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    def add(self, key: str, text: str) -> None:
        if key not in self._seen:
            self._seen.add(key)
            self.lines.append(text)


async def wishlist_add_summary(
    db: object, user_id: int, rows: Iterable[DmListRow]
) -> tuple[str, int]:
    """Add rows to the user's real wishlist; returns (message, added count).

    Goes through the wishlist's own add path, so its cap of 20 and its
    duplicate handling are the wishlist's, unchanged. Rows without a
    real product name (a picture that couldn't be read, a link that
    couldn't be checked) are skipped — a wishlist entry named
    "Item 425031866" could never match a deal.
    """

    added: list[str] = []
    already: list[str] = []
    full = False
    for row in rows:
        if not row.wishlistable:
            continue
        result = await add_user_wishlist_item(db, user_id, row.name)
        if result.added:
            added.append(result.item)
        elif result.reason == "duplicate":
            already.append(result.item)
        elif result.at_cap:
            full = True
            break
    lines: list[str] = []
    if added:
        lines.append(
            "✅ Added to your wishlist: "
            + ", ".join(f"**{name}**" for name in added)
            + ". I'll DM you when the bot's broad scan finds one as a "
            "price-checked deal."
        )
    if already:
        lines.append(
            "Already on your wishlist: "
            + ", ".join(f"**{name}**" for name in already)
            + "."
        )
    if full:
        lines.append(
            f"Your wishlist is full ({MAX_WISHLIST_ITEMS_PER_USER} items). "
            "Remove one with `/wishlist remove` before adding another."
        )
    if not lines:
        lines.append(
            "Nothing there I can add yet — tap **Check prices now** first "
            "so the items have real names and prices."
        )
    return "\n".join(lines), len(added)


def _display_rows(
    *outcomes: object,
) -> tuple[list[DmListRow], frozenset[int]]:
    """Rows a settled reply must SHOW: newly added plus duplicates.

    Duplicates were previously counted but never rendered — an
    all-duplicate settle showed a bare "N of those were already on
    your list" with no item named and every action button disabled,
    because the view was built from the (empty) added rows alone.
    Duplicate rows are real saved rows, so the view built from them
    can wishlist, re-check, and remove them like any other row.
    """

    rows: list[DmListRow] = []
    seen: set[int] = set()
    duplicate_ids: set[int] = set()
    for outcome in outcomes:
        for row in getattr(outcome, "duplicate_rows", ()) or ():
            duplicate_ids.add(int(row.id))
        merged = list(getattr(outcome, "added", ()) or ()) + list(
            getattr(outcome, "duplicate_rows", ()) or ()
        )
        for row in merged:
            if int(row.id) not in seen:
                seen.add(int(row.id))
                rows.append(row)
    return rows, frozenset(duplicate_ids)


class DmListView(discord.ui.View):
    """The tappable list: pick rows, wishlist them, re-check, close.

    Used for the DM intake reply (the newly added rows) and for the
    /menu My list page (the whole saved list). Only the list's owner
    can use it — the menu page is ephemeral, but the check costs
    nothing and the DM copy can be forwarded.
    """

    def __init__(
        self,
        bot: commands.Bot,
        owner_id: int,
        rows: Iterable[DmListRow],
        *,
        title: str = "Added to your list",
        notes: Iterable[str] = (),
        page_price_item_ids: frozenset[str] = frozenset(),
        duplicate_ids: frozenset[int] = frozenset(),
    ) -> None:
        # No timeout: a list message stays tappable for as long as
        # this process lives, and DmListPersistentView (registered at
        # cog load) answers taps on messages whose view died with a
        # restart. A 30-minute timeout used to strand Felix's list
        # messages with buttons that looked live but did nothing.
        super().__init__(timeout=None)
        self.bot = bot
        self.owner_id = int(owner_id)
        self.rows: list[DmListRow] = list(rows)
        self.title = title
        self.notes: list[str] = list(notes)
        self.page_price_item_ids = page_price_item_ids
        self.duplicate_ids = frozenset(duplicate_ids)
        self.check_note = ""
        self.selected_ids: list[int] = []
        self._clear_armed = False

        named = [row for row in self.rows if row.wishlistable][:_SELECT_LIMIT]
        self.select: discord.ui.Select | None = None
        if named:
            options = []
            for row in named:
                if row.status == STATUS_EXACT:
                    price = _money_cents(row.price_cents)
                    description = f"{price} · exact match" if price else "exact match"
                else:
                    description = "closest match — price not checked"
                options.append(
                    discord.SelectOption(
                        label=_short_name(row.name)[:100] or "Item",
                        description=description[:100],
                        value=str(row.id),
                    )
                )
            self.select = discord.ui.Select(
                placeholder="Pick items for your wishlist…",
                min_values=0,
                max_values=len(options),
                options=options,
                custom_id="dm_list:select",
            )
            self.select.callback = self._on_select  # type: ignore[method-assign]
            self.add_item(self.select)

        self.add_selected_button = discord.ui.Button(
            label="Add selected to my wishlist",
            style=discord.ButtonStyle.primary,
            custom_id="dm_list:add_selected",
            disabled=not named,
        )
        self.add_selected_button.callback = self._on_add_selected  # type: ignore[method-assign]
        self.add_item(self.add_selected_button)

        self.add_all_button = discord.ui.Button(
            label="Add all matches to my wishlist",
            style=discord.ButtonStyle.secondary,
            custom_id="dm_list:add_all",
            disabled=not named,
        )
        self.add_all_button.callback = self._on_add_all  # type: ignore[method-assign]
        self.add_item(self.add_all_button)

        self.check_button = discord.ui.Button(
            label="Check prices now",
            style=discord.ButtonStyle.secondary,
            custom_id="dm_list:check",
            disabled=not any(row.item_id for row in self.rows),
        )
        self.check_button.callback = self._on_check  # type: ignore[method-assign]
        self.add_item(self.check_button)

        self.remove_button = discord.ui.Button(
            label="Remove picked",
            style=discord.ButtonStyle.secondary,
            custom_id="dm_list:remove_picked",
            disabled=not named,
        )
        self.remove_button.callback = self._on_remove_picked  # type: ignore[method-assign]
        self.add_item(self.remove_button)

        self.clear_button = discord.ui.Button(
            label="Clear whole list",
            style=discord.ButtonStyle.secondary,
            custom_id="dm_list:clear",
            disabled=not self.rows,
        )
        self.clear_button.callback = self._on_clear_list  # type: ignore[method-assign]
        self.add_item(self.clear_button)

        self.close_button = discord.ui.Button(
            label="Close",
            style=discord.ButtonStyle.danger,
            custom_id="dm_list:close",
        )
        self.close_button.callback = self._on_close  # type: ignore[method-assign]
        self.add_item(self.close_button)

    # -- helpers -----------------------------------------------------

    def current_embed(self) -> discord.Embed:
        notes = list(self.notes)
        tally = self._status_tally()
        if tally:
            notes.insert(0, tally)
        if self.check_note:
            notes.append(self.check_note)
        return build_dm_list_embed(
            self.rows,
            title=self.title,
            notes=notes,
            page_price_item_ids=self.page_price_item_ids,
            duplicate_ids=self.duplicate_ids,
        )

    def _status_tally(self) -> str:
        """One line of counts by match status, so the list reads at a glance."""

        if len(self.rows) < 2:
            return ""
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        parts: list[str] = []
        if counts.get(STATUS_EXACT):
            parts.append(f"{counts[STATUS_EXACT]} exact")
        if counts.get(STATUS_CLOSEST):
            parts.append(f"{counts[STATUS_CLOSEST]} closest (price not checked)")
        if counts.get(STATUS_UNCHECKED):
            parts.append(f"{counts[STATUS_UNCHECKED]} not checked yet")
        if counts.get(STATUS_UNREADABLE):
            parts.append(f"{counts[STATUS_UNREADABLE]} couldn't be read")
        return " · ".join(parts)

    async def _guard_owner(self, interaction: discord.Interaction) -> bool:
        if int(getattr(interaction.user, "id", 0) or 0) == self.owner_id:
            return True
        log.info(
            "DM list: tap refused — user %s is not the owner %s of this list",
            getattr(interaction.user, "id", None),
            self.owner_id,
        )
        await interaction.response.send_message(
            "This is someone else's list — DM me your own pictures or "
            "links to start yours.",
            ephemeral=True,
        )
        return False

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,  # type: ignore[type-arg]
    ) -> None:
        """A failed tap must leave a trace, not silence.

        Tonight's dead buttons produced no log line and no
        error_events row anywhere, so the failure could only be
        found by reading code. Record both, and tell the tapper.
        """

        log.exception(
            "DM list: button tap failed item=%s user=%s",
            getattr(item, "custom_id", None),
            getattr(interaction.user, "id", None),
            exc_info=error,
        )
        db = getattr(self.bot, "db", None)
        if db is not None:
            asyncio.create_task(
                record_exception_event(
                    db,
                    source="dm_list_interaction",
                    error=error,
                    error_id=make_error_id("DMLIST"),
                    context={
                        "custom_id": str(getattr(item, "custom_id", "") or ""),
                        "user_id": str(
                            getattr(interaction.user, "id", "") or ""
                        ),
                    },
                )
            )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "That tap hit a snag — it's logged, so it can be "
                    "found and fixed. Try again in a moment.",
                    ephemeral=True,
                )
        except Exception:  # noqa: BLE001 - the trace above is the point.
            log.exception("DM list: could not answer the failed tap")

    async def _enable_alerts_note(self, interaction: discord.Interaction) -> str:
        """Mirror /wishlist add: first add turns DM alerts on, by probe.

        Returns the extra note for the reply ("" when alerts were
        already on). A failed probe never eats the add itself.
        """

        db = getattr(self.bot, "db", None)
        if db is None:
            return ""
        try:
            preference = await get_dm_deal_alert_preference(db, self.owner_id)
            if preference.enabled:
                return ""
            enabled_preference = replace(
                preference, enabled=True, failure_count=0, last_error=""
            )
            embed = discord.Embed(
                title="✅ SniperPlug wishlist alerts are on",
                description=(
                    "You added an item to your wishlist from your list, so "
                    "your personal deal DMs are now on. "
                    f"{enabled_preference.summary_sentence()} "
                    "Change them anytime in /menu → My alerts."
                ),
                color=discord.Color.green(),
            )
            await interaction.user.send(embed=embed)
            await save_dm_deal_alert_preference(db, enabled_preference)
            return (
                "Your personal deal DMs were off, so I turned them on — "
                f"{enabled_preference.summary_sentence()}"
            )
        except discord.Forbidden:
            return (
                "⚠️ I couldn't DM you, so your personal deal DMs are still "
                "off — the items are saved, but matches can't reach you "
                "until you allow direct messages from server members for a "
                "server we share."
            )
        except Exception:  # noqa: BLE001 - the add already succeeded.
            log.exception("DM list: wishlist alert probe failed")
            return ""

    async def _add_to_wishlist(
        self, interaction: discord.Interaction, rows: list[DmListRow]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.followup.send(
                "I can't reach your wishlist right now — try again in a "
                "little while.",
                ephemeral=True,
            )
            return
        message, added_count = await wishlist_add_summary(db, self.owner_id, rows)
        if added_count:
            note = await self._enable_alerts_note(interaction)
            if note:
                message += f"\n\n{note}"
        await interaction.followup.send(message, ephemeral=True)

    # -- callbacks ---------------------------------------------------

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not await self._guard_owner(interaction):
            return
        assert self.select is not None
        self.selected_ids = [
            int(value) for value in self.select.values if str(value).isdigit()
        ]
        await interaction.response.defer()

    async def _on_add_selected(self, interaction: discord.Interaction) -> None:
        if not await self._guard_owner(interaction):
            return
        if not self.selected_ids:
            log.info(
                "DM list: add-selected tapped with nothing picked user=%s rows=%s",
                getattr(interaction.user, "id", None),
                len(self.rows),
            )
            await interaction.response.send_message(
                "Pick at least one item in the list first, then tap this.",
                ephemeral=True,
            )
            return
        rows = [row for row in self.rows if row.id in set(self.selected_ids)]
        await self._add_to_wishlist(interaction, rows)

    async def _on_add_all(self, interaction: discord.Interaction) -> None:
        if not await self._guard_owner(interaction):
            return
        rows = [row for row in self.rows if row.wishlistable]
        if not rows:
            log.info(
                "DM list: add-all tapped with no wishlistable rows user=%s rows=%s",
                getattr(interaction.user, "id", None),
                len(self.rows),
            )
            await interaction.response.send_message(
                "None of these have a product name I can add yet — tap "
                "**Check prices now** to try pinning them first.",
                ephemeral=True,
            )
            return
        await self._add_to_wishlist(interaction, rows)

    async def _on_check(self, interaction: discord.Interaction) -> None:
        if not await self._guard_owner(interaction):
            return
        await interaction.response.defer()
        db = getattr(self.bot, "db", None)
        provider = provider_registry.get("walmart")
        if provider is None:
            self.check_note = "Walmart search is not connected yet, so prices couldn't be re-checked."
            await self._edit_message(interaction)
            return
        pinned = sorted(
            (row for row in self.rows if row.item_id),
            key=lambda row: row.updated_at or "",
        )[:MAX_RECHECKS_PER_TAP]
        checked = 0
        refreshed = 0
        for row in pinned:
            try:
                result = await verify_single_exact_item(
                    db,
                    provider,
                    str(row.item_id),
                    min_discount=MANUAL_INTAKE_MIN_DISCOUNT,
                )
            except Exception:  # noqa: BLE001 - one bad re-check must not sink the rest.
                log.exception("DM list re-check failed for item %s", row.item_id)
                continue
            checked += 1
            candidate = getattr(result, "candidate", None)
            if candidate is None:
                continue
            price_cents, _is_page = _verified_price_cents(result)
            updated = None
            if db is not None:
                updated = await update_dm_list_item_check(
                    db,
                    row.id,
                    name=str(getattr(candidate, "title", "") or "") or None,
                    price_cents=price_cents,
                    reference_price_cents=_verified_reference_cents(result),
                    status=STATUS_EXACT,
                    product_url=(
                        str(
                            getattr(candidate, "direct_product_url", None)
                            or getattr(candidate, "product_url", "")
                            or ""
                        )
                        or None
                    ),
                )
            if updated is not None:
                self.rows = [
                    updated if existing.id == updated.id else existing
                    for existing in self.rows
                ]
                refreshed += 1
        if refreshed:
            self.check_note = (
                f"✅ Prices re-checked just now — {refreshed} item(s) "
                "updated with the price Walmart shows right now."
            )
        elif checked:
            self.check_note = (
                "I re-checked, but Walmart didn't confirm any of these "
                "just now — the saved prices stay as they were."
            )
        else:
            self.check_note = "None of these could be re-checked just now."
        await self._edit_message(interaction)

    async def _edit_message(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.edit_original_response(
                embed=self.current_embed(), view=self
            )
        except Exception:  # noqa: BLE001 - fall back to the message itself.
            message = getattr(interaction, "message", None)
            if message is not None:
                try:
                    await message.edit(embed=self.current_embed(), view=self)
                except Exception:
                    log.exception("DM list: could not edit the list message")

    async def _on_remove_picked(self, interaction: discord.Interaction) -> None:
        if not await self._guard_owner(interaction):
            return
        if not self.selected_ids:
            log.info(
                "DM list: remove-picked tapped with nothing picked user=%s rows=%s",
                getattr(interaction.user, "id", None),
                len(self.rows),
            )
            await interaction.response.send_message(
                "Pick at least one item in the list first, then tap this.",
                ephemeral=True,
            )
            return
        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.response.send_message(
                "I can't reach your list right now — try again in a "
                "little while.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        removed = await remove_dm_list_items(db, self.owner_id, self.selected_ids)
        removed_ids = set(self.selected_ids)
        remaining = [row for row in self.rows if row.id not in removed_ids]
        new_view = DmListView(
            self.bot,
            self.owner_id,
            remaining,
            title=self.title,
            notes=self.notes,
            page_price_item_ids=self.page_price_item_ids,
            duplicate_ids=self.duplicate_ids - removed_ids,
        )
        new_view.check_note = (
            f"🗑️ Removed {removed} item(s) from your list."
            if removed
            else "Those items were already gone from your list."
        )
        try:
            await interaction.edit_original_response(
                embed=new_view.current_embed(), view=new_view
            )
        except Exception:  # noqa: BLE001 - the removal already landed.
            log.exception("DM list: could not refresh after removal")
        self.stop()

    async def _on_clear_list(self, interaction: discord.Interaction) -> None:
        if not await self._guard_owner(interaction):
            return
        if not self._clear_armed:
            self._clear_armed = True
            self.clear_button.label = "Tap again to clear your whole list"
            await interaction.response.edit_message(view=self)
            return
        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.response.send_message(
                "I can't reach your list right now — try again in a "
                "little while.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        removed = await clear_dm_list_items(db, self.owner_id)
        embed = build_dm_list_embed(
            [],
            title=self.title,
            notes=[
                f"🗑️ Cleared your whole list — {removed} item(s) removed. "
                "DM me pictures or links anytime to start a new one."
            ],
        )
        try:
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception:  # noqa: BLE001 - the clear already landed.
            log.exception("DM list: could not refresh after clear")
        self.stop()

    async def _on_close(self, interaction: discord.Interaction) -> None:
        if not await self._guard_owner(interaction):
            return
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        try:
            await interaction.response.edit_message(view=self)
        except Exception:  # noqa: BLE001 - closing is best-effort.
            log.exception("DM list: close edit failed")
        self.stop()


class DmListPersistentView(discord.ui.View):
    """Restart-proof fallback for DM list buttons.

    Registered once at cog load with the same custom_ids as
    DmListView. Discord routes a tap to the live per-message view
    when one exists; when it doesn't — the bot restarted, or the
    message predates this process — the tap lands here instead of
    dying as an unanswered interaction with no trace anywhere
    (Felix, 2026-11-21: buttons on a settled list reply did
    nothing and nothing was logged).

    The fallback holds no per-message list state: it reloads the
    tapper's OWN saved list from the database and delegates to a
    transient DmListView built from it, so it can never act on
    someone else's rows. Selections are remembered per message id
    so pick-then-tap still works across the two interactions.
    """

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self._selections: dict[int, list[int]] = {}
        self._clear_armed: set[int] = set()

        self.select = discord.ui.Select(
            placeholder="Pick items for your wishlist…",
            min_values=0,
            max_values=25,
            options=[],
            custom_id="dm_list:select",
        )
        self.select.callback = self._on_select  # type: ignore[method-assign]
        self.add_item(self.select)

        buttons = (
            ("Add selected to my wishlist", discord.ButtonStyle.primary, "dm_list:add_selected", self._on_add_selected),
            ("Add all matches to my wishlist", discord.ButtonStyle.secondary, "dm_list:add_all", self._on_add_all),
            ("Check prices now", discord.ButtonStyle.secondary, "dm_list:check", self._on_check),
            ("Remove picked", discord.ButtonStyle.secondary, "dm_list:remove_picked", self._on_remove_picked),
            ("Clear whole list", discord.ButtonStyle.secondary, "dm_list:clear", self._on_clear),
            ("Close", discord.ButtonStyle.danger, "dm_list:close", self._on_close),
        )
        for label, style, custom_id, callback in buttons:
            button = discord.ui.Button(label=label, style=style, custom_id=custom_id)
            button.callback = callback  # type: ignore[method-assign]
            self.add_item(button)

    # -- plumbing ------------------------------------------------------

    @staticmethod
    def _message_id(interaction: discord.Interaction) -> int:
        message = getattr(interaction, "message", None)
        return int(getattr(message, "id", 0) or 0)

    async def _hydrate(
        self, interaction: discord.Interaction, action: str
    ) -> DmListView | None:
        """A DmListView over the tapper's own current list, or None
        after answering the tap with why it can't run."""

        user_id = int(getattr(interaction.user, "id", 0) or 0)
        channel = getattr(interaction, "channel", None)
        if getattr(channel, "type", None) == discord.ChannelType.private:
            recipient = getattr(channel, "recipient", None)
            if (
                recipient is not None
                and int(getattr(recipient, "id", 0) or 0) != user_id
            ):
                await interaction.response.send_message(
                    "This list message isn't in your own DM with me — "
                    "open your list with /menu → My deals → My list.",
                    ephemeral=True,
                )
                return None
        db = getattr(self.bot, "db", None)
        if db is None:
            await interaction.response.send_message(
                "I can't reach your list right now — try again in a "
                "little while.",
                ephemeral=True,
            )
            return None
        rows = await list_dm_list_items(db, user_id)
        log.info(
            "DM list: persistent fallback answered %s tap user=%s message=%s rows=%s",
            action,
            user_id,
            self._message_id(interaction),
            len(rows),
        )
        view = DmListView(self.bot, user_id, rows, title="Your list")
        view.selected_ids = list(
            self._selections.get(self._message_id(interaction), [])
        )
        return view

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,  # type: ignore[type-arg]
    ) -> None:
        log.exception(
            "DM list: persistent fallback tap failed item=%s user=%s",
            getattr(item, "custom_id", None),
            getattr(interaction.user, "id", None),
            exc_info=error,
        )
        db = getattr(self.bot, "db", None)
        if db is not None:
            asyncio.create_task(
                record_exception_event(
                    db,
                    source="dm_list_interaction_fallback",
                    error=error,
                    error_id=make_error_id("DMLIST"),
                    context={
                        "custom_id": str(getattr(item, "custom_id", "") or ""),
                        "user_id": str(
                            getattr(interaction.user, "id", "") or ""
                        ),
                    },
                )
            )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "That tap hit a snag — it's logged, so it can be "
                    "found and fixed. Try again in a moment.",
                    ephemeral=True,
                )
        except Exception:  # noqa: BLE001 - the trace above is the point.
            log.exception("DM list: could not answer the failed fallback tap")

    # -- callbacks -----------------------------------------------------

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self._selections[self._message_id(interaction)] = [
            int(value) for value in self.select.values if str(value).isdigit()
        ]
        log.info(
            "DM list: persistent fallback recorded a pick user=%s message=%s picked=%s",
            getattr(interaction.user, "id", None),
            self._message_id(interaction),
            len(self._selections[self._message_id(interaction)]),
        )
        await interaction.response.defer()

    async def _on_add_selected(self, interaction: discord.Interaction) -> None:
        view = await self._hydrate(interaction, "add_selected")
        if view is not None:
            await view._on_add_selected(interaction)

    async def _on_add_all(self, interaction: discord.Interaction) -> None:
        view = await self._hydrate(interaction, "add_all")
        if view is not None:
            await view._on_add_all(interaction)

    async def _on_check(self, interaction: discord.Interaction) -> None:
        view = await self._hydrate(interaction, "check")
        if view is not None:
            await view._on_check(interaction)

    async def _on_remove_picked(self, interaction: discord.Interaction) -> None:
        view = await self._hydrate(interaction, "remove_picked")
        if view is not None:
            await view._on_remove_picked(interaction)

    async def _on_clear(self, interaction: discord.Interaction) -> None:
        view = await self._hydrate(interaction, "clear")
        if view is None:
            return
        message_id = self._message_id(interaction)
        if message_id in self._clear_armed:
            # Second tap: the armed state lived here, not on the
            # (unregistered) transient view, so arm the transient
            # and let its own handler do the clear.
            self._clear_armed.discard(message_id)
            view._clear_armed = True
        else:
            self._clear_armed.add(message_id)
        await view._on_clear_list(interaction)

    async def _on_close(self, interaction: discord.Interaction) -> None:
        log.info(
            "DM list: persistent fallback answered close tap user=%s message=%s",
            getattr(interaction.user, "id", None),
            self._message_id(interaction),
        )
        try:
            await interaction.response.edit_message(view=None)
        except Exception:  # noqa: BLE001 - closing is best-effort.
            log.exception("DM list: fallback close edit failed")


class DmListIntakeCog(commands.Cog):
    """DM intake listener + the /menu My list page handler."""

    def __init__(
        self,
        bot: commands.Bot,
        config: DmListIntakeConfig | None = None,
        *,
        ocr_engine: OcrEngine | None = None,
        remote_ocr_engine: RemoteOcrEngine | None = None,
    ) -> None:
        self.bot = bot
        self.config = config or DmListIntakeConfig.from_env()
        self.ocr_engine = ocr_engine if ocr_engine is not None else get_ocr_engine()
        self.remote_ocr = (
            remote_ocr_engine if remote_ocr_engine is not None else RemoteOcrEngine()
        )
        # Background tasks settling remote picture reads; kept so the
        # tasks are not garbage-collected mid-flight.
        self._pending_tasks: set[asyncio.Task] = set()
        # Pending-read record ids whose settle is running in THIS
        # process; the recovery pass never touches those.
        self._live_pending_ids: set[int] = set()
        self._recovery_task: asyncio.Task | None = None
        # user id -> monotonic timestamp of the last text-only help reply.
        self._help_replied_at: dict[int, float] = {}

    async def cog_load(self) -> None:
        add_view = getattr(self.bot, "add_view", None)
        if callable(add_view):
            try:
                add_view(DmListPersistentView(self.bot))
                log.info("DM list: persistent button fallback registered")
            except Exception:  # noqa: BLE001 - live views still work without it.
                log.exception(
                    "DM list: could not register the persistent button fallback"
                )
        self._recovery_task = asyncio.create_task(
            self._pending_read_recovery_loop(),
            name="sniperplug-dm-list-pending-read-recovery",
        )

    async def cog_unload(self) -> None:
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            self._recovery_task = None

    # -- the /menu door ----------------------------------------------

    async def open_my_list(self, interaction: discord.Interaction) -> None:
        """``/menu`` → My deals → My list: the saved list, same actions."""

        await interaction.response.defer(ephemeral=True)
        db = getattr(self.bot, "db", None)
        user_id = int(interaction.user.id)
        if db is None:
            await interaction.followup.send(
                "I can't reach your list right now — try again in a little while.",
                ephemeral=True,
            )
            return
        rows = await list_dm_list_items(db, user_id)
        if not rows:
            await interaction.followup.send(
                "Your list is empty. DM me a picture of an item, a "
                "screenshot of a cart or wishlist, or paste Walmart "
                "links, and I'll keep them here.",
                ephemeral=True,
            )
            return
        view = DmListView(
            self.bot,
            user_id,
            rows,
            title="Your list",
            notes=(
                "Prices are from when each item was added or last "
                "checked — tap **Check prices now** for fresh ones.",
            ),
        )
        await interaction.followup.send(
            embed=view.current_embed(), view=view, ephemeral=True
        )

    # -- the DM listener ----------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.config.enabled:
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
        if getattr(message, "guild", None) is not None:
            return
        channel = getattr(message, "channel", None)
        if getattr(channel, "type", None) != discord.ChannelType.private:
            return
        attachments = list(getattr(message, "attachments", ()) or ())
        images = [att for att in attachments if _is_image_attachment(att)]
        item_ids = extract_walmart_item_ids(getattr(message, "content", "") or "")
        if not images and not item_ids:
            await self._help_reply(message, wrong_file=bool(attachments))
            return
        await self._handle_intake(message, item_ids=item_ids, images=images)

    async def _help_reply(self, message: discord.Message, *, wrong_file: bool) -> None:
        author_id = int(getattr(message.author, "id", 0) or 0)
        now = time.monotonic()
        last = self._help_replied_at.get(author_id)
        if last is not None and now - last < HELP_REPLY_COOLDOWN_SECONDS:
            return
        self._help_replied_at[author_id] = now
        await self._reply(
            message, HELP_TEXT_WRONG_FILE if wrong_file else HELP_TEXT
        )

    async def _handle_intake(
        self,
        message: discord.Message,
        *,
        item_ids: list[str],
        images: list,
    ) -> None:
        author_id = int(getattr(message.author, "id", 0) or 0)
        db = getattr(self.bot, "db", None)
        if db is None:
            await self._reply(
                message,
                "I can't save your list right now — try again in a little while.",
            )
            return
        notes = _IntakeNotes()
        provider = provider_registry.get("walmart")
        # The remote reader only matters when a picture might use it:
        # pictures present and no local engine. Its heartbeat is the
        # switch — stale or absent, and everything below is exactly
        # the pre-reader behavior.
        reader_available = False
        if images and not self.ocr_engine.available:
            reader_available = await self.remote_ocr.available(db)
        needs_provider = bool(item_ids) or (
            bool(images) and (self.ocr_engine.available or reader_available)
        )
        if needs_provider:
            gate = await self._provider_gate(provider)
            if gate is not None:
                await self._reply(message, gate)
                return

        budget = _Budget(MAX_VERIFICATIONS_PER_MESSAGE)
        searches = _Budget(MAX_SEARCHES_PER_MESSAGE)
        page_price_item_ids: set[str] = set()
        candidates: list[DmListCandidate] = []

        for item_id in item_ids:
            candidates.append(
                await self._link_candidate(
                    item_id,
                    db=db,
                    provider=provider,
                    budget=budget,
                    page_price_item_ids=page_price_item_ids,
                )
            )
        deferred: list[tuple[object, int]] = []
        for index, attachment in enumerate(images[:MAX_ATTACHMENTS_PER_MESSAGE]):
            if reader_available:
                # Remote path: the size gate applies now (the same
                # rule the local path uses); the read itself settles
                # in the background, so the reply is not held hostage
                # to it. The picture is never downloaded here — the
                # reader fetches it from the attachment URL.
                if int(getattr(attachment, "size", 0) or 0) > MAX_ATTACHMENT_BYTES:
                    notes.add("too_big", TOO_BIG_NOTE)
                    continue
                deferred.append((attachment, index))
                continue
            candidates.extend(
                await self._image_candidates(
                    message,
                    attachment,
                    index,
                    db=db,
                    provider=provider,
                    budget=budget,
                    searches=searches,
                    notes=notes,
                    page_price_item_ids=page_price_item_ids,
                )
            )
        if len(images) > MAX_ATTACHMENTS_PER_MESSAGE:
            notes.add(
                "too_many_pictures",
                f"I only read the first {MAX_ATTACHMENTS_PER_MESSAGE} "
                "pictures in a message — send the rest in another one.",
            )
        if not deferred:
            await self._finish_intake(
                message,
                author_id=author_id,
                db=db,
                candidates=candidates,
                notes=notes,
                budget=budget,
                page_price_item_ids=page_price_item_ids,
            )
            return

        # Remote reads pending: save and show what is already known
        # (the links), say the pictures are being read, and let the
        # background task settle them into the same reply.
        if budget.ran_out:
            notes.add("verify_budget", VERIFY_BUDGET_NOTE)
        first_outcome = await add_dm_list_items(db, author_id, candidates)
        notes.add("reading_picture", reading_picture_note(len(deferred)))
        interim_rows, interim_duplicate_ids = _display_rows(first_outcome)
        view = DmListView(
            self.bot,
            author_id,
            interim_rows,
            title="Added to your list",
            notes=notes.lines,
            page_price_item_ids=frozenset(page_price_item_ids),
            duplicate_ids=interim_duplicate_ids,
        )
        sent = None
        try:
            sent = await message.reply(
                embed=view.current_embed(), view=view, mention_author=False
            )
        except (discord.NotFound, discord.HTTPException, discord.Forbidden):
            log.exception("DM list intake could not reply in DM")
        # Record the pending read BEFORE the settle task starts, so
        # a process death mid-read leaves a recoverable record, not
        # a reply that hangs on "Reading your picture…" forever.
        pending_id: int | None = None
        if sent is not None:
            try:
                pending_id = await record_pending_picture_read(
                    db,
                    user_id=author_id,
                    channel_id=int(getattr(message.channel, "id", 0) or 0),
                    message_id=int(getattr(message, "id", 0) or 0),
                    reply_id=int(getattr(sent, "id", 0) or 0),
                    picture_indexes=[index for _att, index in deferred],
                )
                self._live_pending_ids.add(pending_id)
            except Exception:  # noqa: BLE001 - recovery is a safety net, not a gate.
                pending_id = None
                log.exception("DM list: could not record the pending picture read")
        task = asyncio.create_task(
            self._settle_remote_pictures(
                message=message,
                sent=sent,
                author_id=author_id,
                db=db,
                provider=provider,
                deferred=deferred,
                budget=budget,
                searches=searches,
                notes=notes,
                first_outcome=first_outcome,
                page_price_item_ids=page_price_item_ids,
                pending_id=pending_id,
            )
        )
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _finish_intake(
        self,
        message: discord.Message,
        *,
        author_id: int,
        db: object,
        candidates: list[DmListCandidate],
        notes: _IntakeNotes,
        budget: _Budget,
        page_price_item_ids: set[str],
    ) -> None:
        """Save the candidates and send the list reply (immediate path)."""

        if budget.ran_out:
            notes.add("verify_budget", VERIFY_BUDGET_NOTE)
        outcome = await add_dm_list_items(db, author_id, candidates)
        if outcome.duplicates:
            notes.add(
                "duplicates",
                f"{outcome.duplicates} of those were already on your list.",
            )
        if outcome.cap_reached:
            notes.add("list_full", LIST_FULL_NOTE)
        rows, duplicate_ids = _display_rows(outcome)
        if not rows:
            # Nothing to show: say so honestly, in words, instead of
            # an empty embed or a tally naming nothing.
            text = " ".join(notes.lines) or (
                "I couldn't find any products in what you sent, so "
                "nothing was added. Your list is under "
                "/menu → My deals → My list."
                if not candidates
                else "Those are already on your list — see it under "
                "/menu → My deals → My list."
            )
            await self._reply(message, text)
            return
        view = DmListView(
            self.bot,
            author_id,
            rows,
            title="Added to your list",
            notes=notes.lines,
            page_price_item_ids=frozenset(page_price_item_ids),
            duplicate_ids=duplicate_ids,
        )
        try:
            await message.reply(
                embed=view.current_embed(), view=view, mention_author=False
            )
        except (discord.NotFound, discord.HTTPException, discord.Forbidden):
            log.exception("DM list intake could not reply in DM")

    async def _settle_remote_pictures(
        self,
        *,
        message: discord.Message,
        sent: object,
        author_id: int,
        db: object,
        provider: object,
        deferred: list[tuple[object, int]],
        budget: _Budget,
        searches: _Budget,
        notes: _IntakeNotes,
        first_outcome: object,
        page_price_item_ids: set[str],
        pending_id: int | None = None,
    ) -> None:
        """Settle deferred picture reads, then edit the reply to final.

        Each picture's text runs the same candidate pipeline the
        local engine feeds, under the intake's original budgets —
        the per-message caps cover links and pictures together, no
        matter when the picture finishes. A failed, timed-out, or
        reader-less read settles exactly like the no-engine path:
        an unreadable row plus the "isn't switched on yet" note.

        When the settle was recorded as a pending read, finishing
        here (successfully or with the failure note posted) marks
        the record settled. A settle that never runs to either end
        — the process died — leaves the record for the recovery
        pass, which re-runs this very method against the reply.
        """

        try:
            await self._settle_remote_pictures_inner(
                message=message,
                sent=sent,
                author_id=author_id,
                db=db,
                provider=provider,
                deferred=deferred,
                budget=budget,
                searches=searches,
                notes=notes,
                first_outcome=first_outcome,
                page_price_item_ids=page_price_item_ids,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a background settle must not die loud.
            log.exception("DM list remote picture settle failed")
            # Never leave the reply sitting on "Reading your
            # picture…" forever (2026-11-19: a production save
            # failure did exactly that). Say it failed, plainly,
            # and point at the list and a re-send.
            if sent is not None:
                try:
                    await sent.edit(  # type: ignore[attr-defined]
                        content=SETTLE_FAILURE_NOTE, embed=None, view=None
                    )
                except Exception:  # noqa: BLE001 - best effort only.
                    log.exception(
                        "DM list: could not post the picture-read failure note"
                    )
        if pending_id is not None:
            self._live_pending_ids.discard(pending_id)
            try:
                await mark_pending_picture_read_settled(db, pending_id)
            except Exception:  # noqa: BLE001 - the recovery pass is the backstop.
                log.exception("DM list: could not mark pending read %s settled", pending_id)

    # -- crash recovery for pending picture reads ---------------------

    async def _pending_read_recovery_loop(self) -> None:
        await asyncio.sleep(PENDING_READ_RECOVERY_INITIAL_DELAY_SECONDS)
        while True:
            try:
                await self._recover_pending_reads_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a failed pass retries next interval.
                log.exception("DM list pending-read recovery pass failed")
            await asyncio.sleep(PENDING_READ_RECOVERY_INTERVAL_SECONDS)

    async def _recover_pending_reads_once(self) -> None:
        """Re-settle pending reads whose original settle never finished.

        Runs shortly after boot and then on an interval. A record is
        only eligible past ``PENDING_READ_MIN_AGE_SECONDS`` and when
        no settle for it is live in this process, so the pass can
        never race the read it is recovering.
        """

        db = getattr(self.bot, "db", None)
        if db is None:
            return
        provider = provider_registry.get("walmart")
        if provider is None or await self._provider_gate(provider) is not None:
            # Without search, a recovered settle could only guess;
            # leave the records for a later pass instead.
            return
        records = await list_unsettled_picture_reads(
            db,
            min_age_seconds=PENDING_READ_MIN_AGE_SECONDS,
            limit=PENDING_READ_RECOVERY_BATCH,
        )
        for record in records:
            if record.id in self._live_pending_ids:
                continue
            try:
                await self._recover_one_pending_read(db, provider, record)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad record must not block the rest.
                log.exception(
                    "DM list: pending-read recovery failed for record %s", record.id
                )
        await self._settle_orphaned_jobs(db, provider)

    async def _recover_one_pending_read(self, db, provider, record) -> None:
        try:
            channel = await self.bot.fetch_channel(record.channel_id)  # type: ignore[attr-defined]
            message = await channel.fetch_message(record.message_id)
            sent = await channel.fetch_message(record.reply_id)
        except (discord.NotFound, discord.Forbidden):
            # The conversation or the reply is gone for good: there
            # is nothing left to resolve, so close the record rather
            # than retrying a ghost on every pass.
            log.info(
                "DM list: pending read %s points at a deleted message; closing it",
                record.id,
            )
            await mark_pending_picture_read_settled(db, record.id)
            return
        images = [
            att
            for att in list(getattr(message, "attachments", ()) or ())
            if _is_image_attachment(att)
        ]
        deferred = [
            (images[index], index)
            for index in record.picture_indexes
            if 0 <= index < len(images)
        ]
        if not deferred:
            await mark_pending_picture_read_settled(db, record.id)
            return
        self._live_pending_ids.add(record.id)
        # The original intake's link rows were already saved and
        # shown before the crash; the recovered settle starts from
        # an empty immediate outcome and settles only the pictures.
        first_outcome = await add_dm_list_items(db, record.user_id, [])
        await self._settle_remote_pictures(
            message=message,
            sent=sent,
            author_id=record.user_id,
            db=db,
            provider=provider,
            deferred=deferred,
            budget=_Budget(MAX_VERIFICATIONS_PER_MESSAGE),
            searches=_Budget(MAX_SEARCHES_PER_MESSAGE),
            notes=_IntakeNotes(),
            first_outcome=first_outcome,
            page_price_item_ids=set(),
            pending_id=record.id,
        )

    async def _settle_orphaned_jobs(self, db, provider) -> None:
        """Settle finished reader jobs whose waiter died before consuming.

        These have no pending-read record (they predate records, or
        the waiter died before it could record one), so there is no
        reply to edit — but the read itself finished, and its items
        still belong on the user's list. Settle from the stored
        text through the same candidate pipeline, then consume.
        """

        jobs = await list_orphaned_finished_jobs(
            db,
            min_age_seconds=ORPHANED_JOB_MIN_AGE_SECONDS,
            limit=PENDING_READ_RECOVERY_BATCH,
        )
        for job in jobs:
            source_ref = f"orphan-job:{job.id}"
            try:
                if job.status == JOB_DONE and job.result_text:
                    candidates = await self._candidates_from_text(
                        job.result_text,
                        db=db,
                        provider=provider,
                        budget=_Budget(MAX_VERIFICATIONS_PER_MESSAGE),
                        searches=_Budget(MAX_SEARCHES_PER_MESSAGE),
                        page_price_item_ids=set(),
                        source_ref=source_ref,
                    )
                else:
                    candidates = [
                        DmListCandidate(
                            name="",
                            status=STATUS_UNREADABLE,
                            source="photo",
                            source_ref=source_ref,
                        )
                    ]
                await add_dm_list_items(db, job.user_id, candidates)
                await consume_dm_ocr_job(db, job.id)
                log.info(
                    "DM list: settled orphaned picture job %s for user %s",
                    job.id,
                    job.user_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - leave it for the next pass / stale sweep.
                log.exception("DM list: orphaned job %s could not settle", job.id)

    async def _settle_remote_pictures_inner(
        self,
        *,
        message: discord.Message,
        sent: object,
        author_id: int,
        db: object,
        provider: object,
        deferred: list[tuple[object, int]],
        budget: _Budget,
        searches: _Budget,
        notes: _IntakeNotes,
        first_outcome: object,
        page_price_item_ids: set[str],
    ) -> None:
        pic_candidates: list[DmListCandidate] = []
        read_failed = False
        for attachment, index in deferred:
            source_ref = f"{getattr(message, 'id', 0)}:{index}"
            url = str(getattr(attachment, "url", "") or "")
            text: str | None = None
            if url:
                try:
                    text = await self.remote_ocr.read_text(
                        db, user_id=author_id, attachment_url=url
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - one bad read, one unreadable row.
                    log.exception("DM list remote picture read failed")
                    text = None
            if text:
                pic_candidates.extend(
                    await self._candidates_from_text(
                        text,
                        db=db,
                        provider=provider,
                        budget=budget,
                        searches=searches,
                        page_price_item_ids=page_price_item_ids,
                        source_ref=source_ref,
                    )
                )
                continue
            # The reader was up at intake but the read itself failed,
            # timed out, or lost the reader mid-flight.
            read_failed = True
            pic_candidates.append(
                DmListCandidate(
                    name="",
                    status=STATUS_UNREADABLE,
                    source="photo",
                    source_ref=source_ref,
                )
            )
        if read_failed:
            notes.add("ocr_off", OCR_OFF_NOTE)
        if budget.ran_out:
            notes.add("verify_budget", VERIFY_BUDGET_NOTE)
        outcome = await add_dm_list_items(db, author_id, pic_candidates)
        duplicates = int(getattr(first_outcome, "duplicates", 0) or 0) + int(
            outcome.duplicates or 0
        )
        if duplicates:
            notes.add(
                "duplicates",
                f"{duplicates} of those were already on your list.",
            )
        if getattr(first_outcome, "cap_reached", False) or outcome.cap_reached:
            notes.add("list_full", LIST_FULL_NOTE)
        if not pic_candidates and not read_failed:
            # The picture read fine but held no product lines: the
            # settled reply must say that, not show an empty list.
            notes.add(
                "nothing_found",
                "I read the picture, but I couldn't find any products "
                "in it to add.",
            )
        if sent is None:
            return
        reading_note = reading_picture_note(len(deferred))
        final_notes = [line for line in notes.lines if line != reading_note]
        rows, duplicate_ids = _display_rows(first_outcome, outcome)
        view = DmListView(
            self.bot,
            author_id,
            rows,
            title="Added to your list",
            notes=final_notes,
            page_price_item_ids=frozenset(page_price_item_ids),
            duplicate_ids=duplicate_ids,
        )
        try:
            await sent.edit(embed=view.current_embed(), view=view)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - the rows are saved either way.
            log.exception("DM list: could not update the reply after picture reading")

    # -- pieces ---------------------------------------------------------

    @staticmethod
    async def _provider_gate(provider: object) -> str | None:
        """None when the Walmart provider can run checks, else the reply."""

        if provider is None:
            return "Walmart search is not connected yet."
        try:
            health = await provider.healthcheck()  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001 - report, don't crash the listener.
            log.exception("DM list intake healthcheck failed: %s", error)
            return "Deal search is not ready yet. Try again later."
        if getattr(health, "status", None) != ProviderStatus.READY:
            return (
                "Deal search is not ready yet. Staff needs to finish "
                "the Walmart connection first."
            )
        return None

    async def _verify(
        self, db: object, provider: object, item_id: str
    ) -> object | None:
        try:
            return await verify_single_exact_item(
                db,
                provider,
                item_id,
                min_discount=MANUAL_INTAKE_MIN_DISCOUNT,
            )
        except Exception:  # noqa: BLE001 - one bad item must not drop the rest.
            log.exception("DM list intake verification failed for item %s", item_id)
            return None

    async def _link_candidate(
        self,
        item_id: str,
        *,
        db: object,
        provider: object,
        budget: _Budget,
        page_price_item_ids: set[str],
    ) -> DmListCandidate:
        url = f"https://www.walmart.com/ip/{item_id}"
        if not budget.spend():
            return DmListCandidate(
                name=f"Item {item_id}",
                status=STATUS_UNCHECKED,
                item_id=item_id,
                product_url=url,
                source="link",
            )
        result = await self._verify(db, provider, item_id)
        candidate = getattr(result, "candidate", None) if result else None
        if candidate is None:
            return DmListCandidate(
                name=f"Item {item_id}",
                status=STATUS_UNCHECKED,
                item_id=item_id,
                product_url=url,
                source="link",
            )
        price_cents, is_page = _verified_price_cents(result)
        if is_page:
            page_price_item_ids.add(item_id)
        return DmListCandidate(
            name=str(getattr(candidate, "title", "") or "") or f"Item {item_id}",
            status=STATUS_EXACT,
            item_id=item_id,
            price_cents=price_cents,
            reference_price_cents=_verified_reference_cents(result),
            product_url=(
                str(
                    getattr(candidate, "direct_product_url", None)
                    or getattr(candidate, "product_url", "")
                    or ""
                )
                or url
            ),
            source="link",
        )

    async def _image_candidates(
        self,
        message: discord.Message,
        attachment: object,
        index: int,
        *,
        db: object,
        provider: object,
        budget: _Budget,
        searches: _Budget,
        notes: _IntakeNotes,
        page_price_item_ids: set[str],
    ) -> list[DmListCandidate]:
        source_ref = f"{getattr(message, 'id', 0)}:{index}"
        unreadable = DmListCandidate(
            name="",
            status=STATUS_UNREADABLE,
            source="photo",
            source_ref=source_ref,
        )
        size = int(getattr(attachment, "size", 0) or 0)
        if size > MAX_ATTACHMENT_BYTES:
            notes.add("too_big", TOO_BIG_NOTE)
            return []
        if not self.ocr_engine.available:
            notes.add("ocr_off", OCR_OFF_NOTE)
            return [unreadable]
        try:
            data = await attachment.read()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - an unreadable download is one bad picture.
            log.exception("DM list intake could not download a picture")
            return [unreadable]
        if data is None or len(data) > MAX_ATTACHMENT_BYTES:
            notes.add("too_big", TOO_BIG_NOTE)
            return []
        try:
            text = await asyncio.to_thread(self.ocr_engine.extract_text, bytes(data))
        except Exception:  # noqa: BLE001 - engine failure is one unreadable picture.
            log.exception("DM list OCR engine failed on one picture")
            text = None
        if not text:
            return [unreadable]
        return await self._candidates_from_text(
            text,
            db=db,
            provider=provider,
            budget=budget,
            searches=searches,
            page_price_item_ids=page_price_item_ids,
            source_ref=source_ref,
        )

    async def _candidates_from_text(
        self,
        text: str,
        *,
        db: object,
        provider: object,
        budget: _Budget,
        searches: _Budget,
        page_price_item_ids: set[str],
        source_ref: str,
    ) -> list[DmListCandidate]:
        """Raw OCR text → searched, verified rows (or one unreadable).

        Shared by the local-engine path and the remote reader path:
        whichever engine produced the text, the lines go through the
        same search → verify pipeline under the same budgets.
        """

        unreadable = DmListCandidate(
            name="",
            status=STATUS_UNREADABLE,
            source="photo",
            source_ref=source_ref,
        )
        lines = extract_candidate_lines(text)
        if not lines:
            return [unreadable]
        rows: list[DmListCandidate] = []
        for line in lines:
            if not searches.spend():
                break
            row = await self._search_candidate(
                line,
                db=db,
                provider=provider,
                budget=budget,
                page_price_item_ids=page_price_item_ids,
            )
            if row is not None:
                rows.append(row)
        return rows or [unreadable]

    async def _search_candidate(
        self,
        line: str,
        *,
        db: object,
        provider: object,
        budget: _Budget,
        page_price_item_ids: set[str],
    ) -> DmListCandidate | None:
        """One OCR line: search it, then pin the best hit if budget allows."""

        try:
            result = await provider.scan(  # type: ignore[attr-defined]
                ProviderScanRequest(
                    source_key="walmart",
                    query=line,
                    max_results=3,
                    metadata={"requested_by": "dm_list"},
                )
            )
        except Exception:  # noqa: BLE001 - one bad search must not drop the picture.
            log.exception("DM list search failed for line %r", line)
            return None
        found = getattr(result, "candidates", ()) or ()
        if not found:
            return None
        best = _best_title_match(line, found)
        if best is None:
            # The search answered, but nothing it returned is the
            # product in the picture. A wrong "exact match" is worse
            # than no row — the line simply contributes nothing.
            return None
        name = str(getattr(best, "title", "") or "").strip() or line
        item_id = _candidate_item_id(best)
        url = (
            str(
                getattr(best, "direct_product_url", None)
                or getattr(best, "product_url", "")
                or ""
            )
            or None
        )
        if item_id is None or not budget.spend():
            return DmListCandidate(
                name=name,
                status=STATUS_CLOSEST,
                item_id=item_id,
                product_url=url,
                source="photo",
            )
        verified = await self._verify(db, provider, item_id)
        candidate = getattr(verified, "candidate", None) if verified else None
        if candidate is None:
            return DmListCandidate(
                name=name,
                status=STATUS_CLOSEST,
                item_id=item_id,
                product_url=url,
                source="photo",
            )
        price_cents, is_page = _verified_price_cents(verified)
        if is_page:
            page_price_item_ids.add(item_id)
        return DmListCandidate(
            name=str(getattr(candidate, "title", "") or "") or name,
            status=STATUS_EXACT,
            item_id=item_id,
            price_cents=price_cents,
            reference_price_cents=_verified_reference_cents(verified),
            product_url=(
                str(
                    getattr(candidate, "direct_product_url", None)
                    or getattr(candidate, "product_url", "")
                    or ""
                )
                or url
            ),
            source="photo",
        )

    @staticmethod
    async def _reply(message: discord.Message, content: str) -> None:
        try:
            await message.reply(content, mention_author=False)
        except (discord.NotFound, discord.HTTPException, discord.Forbidden):
            log.exception("DM list intake could not reply in DM")
