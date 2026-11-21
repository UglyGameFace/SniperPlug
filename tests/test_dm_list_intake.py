"""DM list intake cog: gating, links, pictures, the menu, the handoff.

Fixture-based only — no Discord connection, no live Walmart calls.
Verification and search are stubbed; the OCR engine is a fake behind
the real interface (the production container has no engine today —
see ``sniperplug.services.dm_list_ocr``). Screenshot fixtures are
generated in-test: rendered with Pillow when it is importable, else
built as a valid PNG with the standard library. No binary fixtures.
"""

from __future__ import annotations

import contextlib
import io
import struct
import zlib
from types import SimpleNamespace

import aiosqlite
import discord
import pytest

import sniperplug.cogs.dm_list_intake as intake
import sniperplug.services.dm_deal_alerts as alerts
import sniperplug.services.dm_lists as dm_lists
import sniperplug.services.user_wishlist as wishlist
from sniperplug.cogs.dm_list_intake import (
    DmListIntakeCog,
    DmListIntakeConfig,
    DmListView,
    wishlist_add_summary,
)
from sniperplug.cogs.unified_menu import MENU_DELEGATES
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.dm_deal_alerts import get_dm_deal_alert_preference
from sniperplug.services.dm_list_ocr import OcrEngine, UnavailableOcrEngine
from sniperplug.services.dm_lists import (
    STATUS_CLOSEST,
    STATUS_EXACT,
    STATUS_UNCHECKED,
    STATUS_UNREADABLE,
    list_dm_list_items,
)
from sniperplug.services.user_wishlist import (
    MAX_WISHLIST_ITEMS_PER_USER,
    add_user_wishlist_item,
    list_user_wishlist,
)
from sniperplug.services.walmart_exact_verification_queue import (
    SingleExactVerification,
)

USER_ID = 111
BOT_ID = 999
IPHONE_URL = "https://www.walmart.com/ip/Apple-iPhone-11-AT-T-64GB-Green/425031866"
IPHONE_ID = "425031866"
NINJA_ID = "987654321"

CART_OCR_TEXT = (
    "Cart (2 items)\n"
    "Apple iPhone 11, 64GB, Green -\n"
    "Unlocked\n"
    "$298.00\n"
    "Qty 1\n"
    "Pickup tomorrow\n"
    "Ninja AF101 Air Fryer, 4 Quart\n"
    "$89.99\n"
    "Qty 1\n"
    "Subtotal\n"
)


# --- screenshot fixture -----------------------------------------------------


def _stdlib_png_bytes(width: int = 64, height: int = 64) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _cart_screenshot_bytes() -> bytes:
    """A generated cart screenshot (Pillow when available, else stdlib)."""

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return _stdlib_png_bytes(600, 800)
    image = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "Cart (2 items)")
    draw.text((20, 80), "Apple iPhone 11, 64GB, Green")
    draw.text((20, 110), "$298.00")
    draw.text((20, 160), "Ninja AF101 Air Fryer, 4 Quart")
    draw.text((20, 190), "$89.99")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --- fakes ------------------------------------------------------------------


class _FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


@contextlib.asynccontextmanager
async def _isolated_db():
    dm_lists._SCHEMA_READY = False
    wishlist._SCHEMA_READY = False
    alerts._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    try:
        yield _FakeDatabase(conn)
    finally:
        await conn.close()
        dm_lists._SCHEMA_READY = False
        wishlist._SCHEMA_READY = False
        alerts._SCHEMA_READY = False


class FakeBot:
    def __init__(self, db=None) -> None:
        self.db = db
        self.user = SimpleNamespace(id=BOT_ID)


class FakeAttachment:
    def __init__(
        self,
        data: bytes = b"",
        *,
        filename: str = "cart.png",
        content_type: str = "image/png",
        size: int | None = None,
    ) -> None:
        self._data = data
        self.filename = filename
        self.content_type = content_type
        self.size = len(data) if size is None else size
        self.read_calls = 0

    async def read(self) -> bytes:
        self.read_calls += 1
        return self._data


class FakeMessage:
    def __init__(
        self,
        content: str = "",
        *,
        guild=None,
        channel_type=discord.ChannelType.private,
        author_bot: bool = False,
        author_id: int = USER_ID,
        attachments=(),
        message_id: int = 555,
    ) -> None:
        self.content = content
        self.guild = guild
        self.channel = SimpleNamespace(type=channel_type)
        self.author = SimpleNamespace(bot=author_bot, id=author_id)
        self.attachments = list(attachments)
        self.id = message_id
        self.replies: list[dict] = []

    async def reply(self, content=None, **kwargs):
        self.replies.append({"content": content, **kwargs})
        return SimpleNamespace(id=777)


def _candidate(item_id: str, title: str, price: float, **overrides):
    fields = {
        "source_key": "walmart",
        "retailer": "walmart",
        "title": title,
        "product_url": f"https://www.walmart.com/ip/{item_id}",
        "api_current_price": price,
        "api_reference_price": price * 2,
    }
    fields.update(overrides)
    return SourceCandidate(**fields)


class VerifyStub:
    """Stands in for verify_single_exact_item; per-ID canned outcomes."""

    def __init__(self, *, fail_ids=(), price: float = 298.0) -> None:
        self.calls: list[str] = []
        self.fail_ids = set(fail_ids)
        self.price = price

    async def __call__(self, db, provider, item_id, *, min_discount):
        self.calls.append(item_id)
        if item_id in self.fail_ids:
            return SingleExactVerification(
                item_id=item_id, status="retry", error="boom", enqueued=True
            )
        titles = {
            IPHONE_ID: "Apple iPhone 11, 64GB, Green",
            NINJA_ID: "Ninja AF101 Air Fryer, 4 Quart",
        }
        return SingleExactVerification(
            item_id=item_id,
            candidate=_candidate(
                item_id, titles.get(item_id, f"Product {item_id}"), self.price
            ),
            classification="verified_markdown",
            status="verified",
            enqueued=True,
        )


class FakeProvider:
    def __init__(self, *, ready: bool = True, search_results=None) -> None:
        self._ready = ready
        self.search_results = search_results or {}
        self.queries: list[str] = []

    async def healthcheck(self):
        status = ProviderStatus.READY if self._ready else ProviderStatus.ERROR
        return SimpleNamespace(status=status)

    async def scan(self, request):
        self.queries.append(request.query)
        return SimpleNamespace(
            candidates=tuple(self.search_results.get(request.query, ()))
        )


class FakeOcrEngine(OcrEngine):
    available = True

    def __init__(self, text: str) -> None:
        self.text = text
        self.seen: list[bytes] = []

    def extract_text(self, image_bytes: bytes) -> str:
        self.seen.append(bytes(image_bytes))
        return self.text


def _cog(monkeypatch, db, *, verify=None, provider=None, engine=None):
    verify = verify if verify is not None else VerifyStub()
    provider = provider if provider is not None else FakeProvider()
    monkeypatch.setattr(intake, "verify_single_exact_item", verify)
    monkeypatch.setattr(provider_registry, "get", lambda key: provider)
    cog = DmListIntakeCog(
        FakeBot(db),
        DmListIntakeConfig(enabled=True),
        ocr_engine=engine if engine is not None else UnavailableOcrEngine(),
    )
    return cog, verify, provider


# --- gating -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guild_message_ignored(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, verify, _ = _cog(monkeypatch, db)
        message = FakeMessage(IPHONE_URL, guild=SimpleNamespace(id=42))
        await cog.on_message(message)
        assert message.replies == []
        assert verify.calls == []


@pytest.mark.asyncio
async def test_group_dm_and_bot_authors_ignored(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, verify, _ = _cog(monkeypatch, db)
        group = FakeMessage(IPHONE_URL, channel_type=discord.ChannelType.group)
        await cog.on_message(group)
        bot_msg = FakeMessage(IPHONE_URL, author_bot=True)
        await cog.on_message(bot_msg)
        own = FakeMessage(IPHONE_URL, author_id=BOT_ID)
        await cog.on_message(own)
        assert group.replies == bot_msg.replies == own.replies == []
        assert verify.calls == []


@pytest.mark.asyncio
async def test_text_only_dm_gets_one_throttled_help_reply(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, _, _ = _cog(monkeypatch, db)
        first = FakeMessage("hello there")
        await cog.on_message(first)
        assert len(first.replies) == 1
        assert "picture of an item" in first.replies[0]["content"]
        second = FakeMessage("are you there")
        await cog.on_message(second)
        assert second.replies == []  # throttled: one reply per burst


@pytest.mark.asyncio
async def test_wrong_file_type_dm_gets_the_file_help_reply(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, _, _ = _cog(monkeypatch, db)
        attachment = FakeAttachment(
            b"plain text", filename="list.txt", content_type="text/plain"
        )
        message = FakeMessage("", attachments=[attachment])
        await cog.on_message(message)
        assert len(message.replies) == 1
        assert "png, jpg, webp" in message.replies[0]["content"]


# --- links --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_links_become_exact_rows_with_a_menu(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, verify, _ = _cog(monkeypatch, db)
        message = FakeMessage(
            f"here you go {IPHONE_URL} and https://www.walmart.com/ip/{NINJA_ID} thanks"
        )
        await cog.on_message(message)
        assert verify.calls == [IPHONE_ID, NINJA_ID]
        assert len(message.replies) == 1
        reply = message.replies[0]
        embed = reply["embed"]
        assert "Apple iPhone 11, 64GB, Green" in embed.description
        assert "$298" in embed.description
        assert "exact match" in embed.description
        view = reply["view"]
        assert view.select is not None
        assert len(view.select.options) == 2
        rows = await list_dm_list_items(db, USER_ID)
        assert [row.status for row in rows] == [STATUS_EXACT, STATUS_EXACT]
        assert rows[0].price_cents == 29800


@pytest.mark.asyncio
async def test_failed_link_check_becomes_an_unchecked_row(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, _, _ = _cog(monkeypatch, db, verify=VerifyStub(fail_ids={IPHONE_ID}))
        message = FakeMessage(IPHONE_URL)
        await cog.on_message(message)
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1
        assert rows[0].status == STATUS_UNCHECKED
        assert rows[0].price_cents is None
        assert "couldn't check" in message.replies[0]["embed"].description


@pytest.mark.asyncio
async def test_resent_link_is_a_duplicate_not_a_second_row(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, _, _ = _cog(monkeypatch, db)
        await cog.on_message(FakeMessage(IPHONE_URL))
        second = FakeMessage(IPHONE_URL, message_id=556)
        await cog.on_message(second)
        assert "already on your list" in second.replies[0]["content"]
        assert len(await list_dm_list_items(db, USER_ID)) == 1


@pytest.mark.asyncio
async def test_verification_budget_caps_live_checks_not_rows(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, verify, _ = _cog(monkeypatch, db)
        ids = [str(100000000 + n) for n in range(12)]
        content = " ".join(f"https://www.walmart.com/ip/{item_id}" for item_id in ids)
        message = FakeMessage(content)
        await cog.on_message(message)
        assert len(verify.calls) == intake.MAX_VERIFICATIONS_PER_MESSAGE
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 12
        statuses = [row.status for row in rows]
        assert statuses.count(STATUS_EXACT) == 10
        assert statuses.count(STATUS_UNCHECKED) == 2
        assert "first 10" in message.replies[0]["embed"].description


@pytest.mark.asyncio
async def test_provider_down_replies_plainly_and_saves_nothing(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, verify, _ = _cog(monkeypatch, db, provider=FakeProvider(ready=False))
        message = FakeMessage(IPHONE_URL)
        await cog.on_message(message)
        assert "not ready yet" in message.replies[0]["content"]
        assert verify.calls == []
        assert await list_dm_list_items(db, USER_ID) == ()


# --- pictures -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_picture_without_an_engine_is_saved_unread(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, verify, _ = _cog(monkeypatch, db)  # UnavailableOcrEngine
        attachment = FakeAttachment(_cart_screenshot_bytes())
        message = FakeMessage("", attachments=[attachment])
        await cog.on_message(message)
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1
        assert rows[0].status == STATUS_UNREADABLE
        assert attachment.read_calls == 0  # never downloaded: no engine to feed
        description = message.replies[0]["embed"].description
        assert "couldn't read" in description
        assert "isn't switched on yet" in description
        assert verify.calls == []


@pytest.mark.asyncio
async def test_screenshot_lines_are_searched_then_pinned(monkeypatch) -> None:
    async with _isolated_db() as db:
        engine = FakeOcrEngine(CART_OCR_TEXT)
        provider = FakeProvider(
            search_results={
                "Apple iPhone 11, 64GB, Green": (
                    _candidate(IPHONE_ID, "Apple iPhone 11, 64GB, Green", 310.0),
                ),
                "Ninja AF101 Air Fryer, 4 Quart": (
                    _candidate(NINJA_ID, "Ninja AF101 Air Fryer, 4 Quart", 95.0),
                ),
            }
        )
        cog, verify, _ = _cog(monkeypatch, db, provider=provider, engine=engine)
        attachment = FakeAttachment(_cart_screenshot_bytes())
        message = FakeMessage("", attachments=[attachment])
        await cog.on_message(message)
        # The engine got real PNG bytes; only product lines were searched.
        assert engine.seen and engine.seen[0][:8] == b"\x89PNG\r\n\x1a\n"
        assert provider.queries == [
            "Apple iPhone 11, 64GB, Green",
            "Ninja AF101 Air Fryer, 4 Quart",
        ]
        assert verify.calls == [IPHONE_ID, NINJA_ID]
        rows = await list_dm_list_items(db, USER_ID)
        assert [row.status for row in rows] == [STATUS_EXACT, STATUS_EXACT]
        # Prices come from the verification, not the search or the photo.
        assert rows[0].price_cents == 29800


@pytest.mark.asyncio
async def test_unpinnable_search_hit_is_a_closest_match_without_price(
    monkeypatch,
) -> None:
    async with _isolated_db() as db:
        engine = FakeOcrEngine("Ninja AF101 Air Fryer, 4 Quart\n$89.99\n")
        provider = FakeProvider(
            search_results={
                "Ninja AF101 Air Fryer, 4 Quart": (
                    _candidate(NINJA_ID, "Ninja AF101 Air Fryer, 4 Quart", 95.0),
                )
            }
        )
        cog, _, _ = _cog(
            monkeypatch,
            db,
            provider=provider,
            engine=engine,
            verify=VerifyStub(fail_ids={NINJA_ID}),
        )
        message = FakeMessage("", attachments=[FakeAttachment(b"png-bytes")])
        await cog.on_message(message)
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1
        assert rows[0].status == STATUS_CLOSEST
        assert rows[0].price_cents is None  # no price was ever proven
        description = message.replies[0]["embed"].description
        assert "closest match" in description
        assert "$" not in description


@pytest.mark.asyncio
async def test_noise_only_screenshot_becomes_one_unreadable_row(monkeypatch) -> None:
    async with _isolated_db() as db:
        engine = FakeOcrEngine("Subtotal\n$12.99\nQty 2\nCheckout\n")
        cog, _, provider = _cog(monkeypatch, db, engine=engine)
        message = FakeMessage("", attachments=[FakeAttachment(b"png-bytes")])
        await cog.on_message(message)
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1
        assert rows[0].status == STATUS_UNREADABLE
        assert provider.queries == []


@pytest.mark.asyncio
async def test_oversize_picture_is_noted_not_saved(monkeypatch) -> None:
    async with _isolated_db() as db:
        engine = FakeOcrEngine(CART_OCR_TEXT)
        cog, _, _ = _cog(monkeypatch, db, engine=engine)
        attachment = FakeAttachment(b"x", size=intake.MAX_ATTACHMENT_BYTES + 1)
        message = FakeMessage("", attachments=[attachment])
        await cog.on_message(message)
        assert attachment.read_calls == 0
        assert await list_dm_list_items(db, USER_ID) == ()
        assert "too big" in message.replies[0]["content"]


# --- the view: wishlist handoff + re-check -------------------------------------


class FakeInteractionResponse:
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

    def is_done(self) -> bool:
        return self.deferred or bool(self.sent)


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send(self, content=None, **kwargs) -> None:
        self.sent.append((content, kwargs))


class FakeInteraction:
    def __init__(self, user_id: int = USER_ID) -> None:
        self.user = SimpleNamespace(id=user_id, send=self._send_dm)
        self.response = FakeInteractionResponse()
        self.followup = FakeFollowup()
        self.message = None
        self.original_edits: list[dict] = []
        self.dms: list[dict] = []

    async def _send_dm(self, **kwargs) -> None:
        self.dms.append(kwargs)

    async def edit_original_response(self, **kwargs) -> None:
        self.original_edits.append(kwargs)


async def _seed_rows(db) -> list:
    from sniperplug.services.dm_lists import DmListCandidate, add_dm_list_items

    outcome = await add_dm_list_items(
        db,
        USER_ID,
        [
            DmListCandidate(
                name="Apple iPhone 11, 64GB, Green",
                status=STATUS_EXACT,
                item_id=IPHONE_ID,
                price_cents=29800,
                source="link",
            ),
            DmListCandidate(
                name="Item 987654321",
                status=STATUS_UNCHECKED,
                item_id=NINJA_ID,
                source="link",
            ),
            DmListCandidate(
                name="", status=STATUS_UNREADABLE, source="photo", source_ref="555:0"
            ),
        ],
    )
    return list(outcome.added)


@pytest.mark.asyncio
async def test_wishlist_summary_adds_named_rows_only(monkeypatch) -> None:
    async with _isolated_db() as db:
        rows = await _seed_rows(db)
        message, added = await wishlist_add_summary(db, USER_ID, rows)
        assert added == 1
        assert "Added to your wishlist" in message
        assert await list_user_wishlist(db, USER_ID) == (
            "Apple iPhone 11, 64GB, Green",
        )
        # A second pass reports the duplicate in the wishlist's own terms.
        message2, added2 = await wishlist_add_summary(db, USER_ID, rows)
        assert added2 == 0
        assert "Already on your wishlist" in message2


@pytest.mark.asyncio
async def test_wishlist_summary_surfaces_the_cap_plainly(monkeypatch) -> None:
    async with _isolated_db() as db:
        for n in range(MAX_WISHLIST_ITEMS_PER_USER):
            await add_user_wishlist_item(db, USER_ID, f"existing product {n}")
        rows = await _seed_rows(db)
        message, added = await wishlist_add_summary(db, USER_ID, rows)
        assert added == 0
        assert "Your wishlist is full (20 items)" in message


@pytest.mark.asyncio
async def test_add_all_button_adds_and_turns_alerts_on(monkeypatch) -> None:
    async with _isolated_db() as db:
        rows = await _seed_rows(db)
        view = DmListView(FakeBot(db), USER_ID, rows)
        interaction = FakeInteraction()
        await view._on_add_all(interaction)
        assert await list_user_wishlist(db, USER_ID) == (
            "Apple iPhone 11, 64GB, Green",
        )
        followup_text = interaction.followup.sent[0][0]
        assert "Added to your wishlist" in followup_text
        # First wishlist add probes the DM and turns personal alerts on,
        # exactly like /wishlist add does.
        assert interaction.dms, "the probe DM should have been sent"
        preference = await get_dm_deal_alert_preference(db, USER_ID)
        assert preference.enabled is True


@pytest.mark.asyncio
async def test_list_view_tally_counts_each_status(monkeypatch) -> None:
    async with _isolated_db() as db:
        rows = await _seed_rows(db)
        view = DmListView(FakeBot(db), USER_ID, rows)
        tally = "1 exact · 1 not checked yet · 1 couldn't be read"
        assert view._status_tally() == tally
        assert tally in (view.current_embed().description or "")


@pytest.mark.asyncio
async def test_first_add_probe_dm_quotes_the_real_settings(monkeypatch) -> None:
    async with _isolated_db() as db:
        rows = await _seed_rows(db)
        view = DmListView(FakeBot(db), USER_ID, rows)
        interaction = FakeInteraction()
        await view._on_add_all(interaction)
        assert interaction.dms, "the probe DM should have been sent"
        dm_description = interaction.dms[0]["embed"].description or ""
        # The alerts copy used to quote a removed "Smart settings" mode
        # and a 20% floor the defaults never had. It now quotes the
        # preference's own summary sentence — the settings actually saved.
        assert "Smart" not in dm_description
        preference = await get_dm_deal_alert_preference(db, USER_ID)
        assert preference.summary_sentence() in dm_description
        followup_text = interaction.followup.sent[0][0]
        assert "Smart" not in followup_text
        assert preference.summary_sentence() in followup_text


@pytest.mark.asyncio
async def test_add_selected_uses_the_select_values(monkeypatch) -> None:
    async with _isolated_db() as db:
        rows = await _seed_rows(db)
        view = DmListView(FakeBot(db), USER_ID, rows)
        assert view.select is not None
        view.select._values = [str(rows[0].id)]
        picker = FakeInteraction()
        await view._on_select(picker)
        assert view.selected_ids == [rows[0].id]
        await view._on_add_selected(FakeInteraction())
        assert await list_user_wishlist(db, USER_ID) == (
            "Apple iPhone 11, 64GB, Green",
        )


@pytest.mark.asyncio
async def test_view_refuses_someone_elses_taps(monkeypatch) -> None:
    async with _isolated_db() as db:
        rows = await _seed_rows(db)
        view = DmListView(FakeBot(db), USER_ID, rows)
        stranger = FakeInteraction(user_id=222)
        await view._on_add_all(stranger)
        assert "someone else's list" in stranger.response.sent[0][0]
        assert await list_user_wishlist(db, USER_ID) == ()


@pytest.mark.asyncio
async def test_check_prices_now_pins_the_unchecked_row(monkeypatch) -> None:
    async with _isolated_db() as db:
        rows = await _seed_rows(db)
        verify = VerifyStub(price=42.0)
        monkeypatch.setattr(intake, "verify_single_exact_item", verify)
        monkeypatch.setattr(
            provider_registry, "get", lambda key: FakeProvider()
        )
        view = DmListView(FakeBot(db), USER_ID, rows)
        interaction = FakeInteraction()
        await view._on_check(interaction)
        stored = {row.item_id: row for row in await list_dm_list_items(db, USER_ID)}
        assert stored[NINJA_ID].status == STATUS_EXACT
        assert stored[NINJA_ID].price_cents == 4200
        assert "re-checked just now" in view.check_note
        assert interaction.original_edits, "the list message should be edited"


@pytest.mark.asyncio
async def test_select_options_capped_at_25(monkeypatch) -> None:
    async with _isolated_db() as db:
        from sniperplug.services.dm_lists import DmListCandidate, add_dm_list_items

        batch = [
            DmListCandidate(
                name=f"Some Product Number {n}",
                status=STATUS_EXACT,
                item_id=str(100000000 + n),
                price_cents=100,
                source="link",
            )
            for n in range(30)
        ]
        outcome = await add_dm_list_items(db, USER_ID, batch)
        view = DmListView(FakeBot(db), USER_ID, list(outcome.added))
        assert view.select is not None
        assert len(view.select.options) == 25


# --- the /menu door -------------------------------------------------------------


def test_menu_delegate_points_at_the_loaded_cog() -> None:
    assert MENU_DELEGATES["my-list"] == ("DmListIntakeCog", "open_my_list")
    assert callable(getattr(DmListIntakeCog, "open_my_list"))


@pytest.mark.asyncio
async def test_open_my_list_empty_then_filled(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog, _, _ = _cog(monkeypatch, db)
        empty = FakeInteraction()
        await cog.open_my_list(empty)
        assert "Your list is empty" in empty.followup.sent[0][0]

        await _seed_rows(db)
        filled = FakeInteraction()
        await cog.open_my_list(filled)
        content, kwargs = filled.followup.sent[0]
        assert content is None
        assert "Apple iPhone 11" in kwargs["embed"].description
        assert isinstance(kwargs["view"], DmListView)


# --- tappable product links (Felix, 2026-11-19) -------------------------------


def _link_row(**overrides):
    from sniperplug.services.dm_lists import DmListRow

    fields = {
        "id": 1,
        "user_id": 111,
        "name": "Equate Women's 5-Blade Manual Razor Kit",
        "status": "exact",
        "item_id": "139918537",
        "price_cents": 572,
        "product_url": "https://www.walmart.com/ip/139918537",
    }
    fields.update(overrides)
    return DmListRow(**fields)


def test_row_line_links_the_name_to_the_product_page() -> None:
    from sniperplug.cogs.dm_list_intake import dm_list_row_line

    line = dm_list_row_line(_link_row())
    assert (
        "[**Equate Women's 5-Blade Manual Razor Kit**]"
        "(https://www.walmart.com/ip/139918537)"
    ) in line
    assert "$5.72" in line
    assert "exact match" in line


def test_row_line_links_closest_matches_too() -> None:
    from sniperplug.cogs.dm_list_intake import dm_list_row_line

    line = dm_list_row_line(
        _link_row(status="closest", price_cents=None, name="Bounty Paper Towels")
    )
    assert "[**Bounty Paper Towels**](https://www.walmart.com/ip/139918537)" in line
    assert "closest match" in line
    assert "$" not in line


def test_row_line_without_a_url_stays_plain() -> None:
    from sniperplug.cogs.dm_list_intake import dm_list_row_line

    line = dm_list_row_line(_link_row(product_url=None))
    assert "**Equate Women's 5-Blade Manual Razor Kit**" in line
    assert "](" not in line


def test_row_line_strips_brackets_from_linked_names() -> None:
    from sniperplug.cogs.dm_list_intake import dm_list_row_line

    line = dm_list_row_line(_link_row(name="Widget [2 Pack] Special"))
    assert "[**Widget 2 Pack Special**](https://www.walmart.com/ip/139918537)" in line


# --- sale labels (Felix, 2026-11-19) ------------------------------------------


def test_row_line_shows_the_was_price_and_percent_off() -> None:
    from sniperplug.cogs.dm_list_intake import dm_list_row_line

    line = dm_list_row_line(_link_row(reference_price_cents=1199))
    assert "$5.72" in line
    assert "was $11.99" in line
    assert "52% off" in line
    assert "not on sale" not in line


def test_row_line_says_not_on_sale_without_a_proven_discount() -> None:
    from sniperplug.cogs.dm_list_intake import dm_list_row_line

    line = dm_list_row_line(_link_row())
    assert "$5.72" in line
    assert "not on sale right now" in line
    assert "% off" not in line


def test_row_line_page_price_still_says_not_on_sale() -> None:
    from sniperplug.cogs.dm_list_intake import dm_list_row_line

    line = dm_list_row_line(_link_row(), page_price=True)
    assert "(page price)" in line
    assert "not on sale right now" in line


@pytest.mark.asyncio
async def test_verified_markdown_reference_reaches_the_row_and_reply(
    monkeypatch,
) -> None:
    async with _isolated_db() as db:
        cog, verify, _ = _cog(monkeypatch, db)
        message = FakeMessage(IPHONE_URL)
        await cog.on_message(message)
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1
        # The stub verifies at $298 against a $596 reference.
        assert rows[0].price_cents == 29800
        assert rows[0].reference_price_cents == 59600
        description = message.replies[0]["embed"].description
        assert "was $596" in description
        assert "50% off" in description


# --- the title-match gate (Felix, 2026-11-19) ---------------------------------


@pytest.mark.asyncio
async def test_unrelated_search_hit_is_never_pinned(monkeypatch) -> None:
    """A line that matches nothing the search returned contributes
    no row — a wrong 'exact match' is worse than none."""

    async with _isolated_db() as db:
        engine = FakeOcrEngine("Ask a question about this product\n")
        provider = FakeProvider(
            search_results={
                "Ask a question about this product": (
                    _candidate(
                        "161147779",
                        "If You Know You Know IYKYK - The Question Card "
                        "Party Game for Adults",
                        19.99,
                    ),
                )
            }
        )
        cog, verify, _ = _cog(monkeypatch, db, provider=provider, engine=engine)
        message = FakeMessage("", attachments=[FakeAttachment(b"png-bytes")])
        await cog.on_message(message)
        assert verify.calls == []
        rows = await list_dm_list_items(db, USER_ID)
        assert [row.status for row in rows] == [STATUS_UNREADABLE]


@pytest.mark.asyncio
async def test_search_picks_the_hit_that_matches_the_line(monkeypatch) -> None:
    """The first hit is not taken on faith: the garbled Capstar
    fragment must pin the Capstar hit listed second, not the
    PetArmor hit listed first (measured pair, 2026-11-19)."""

    async with _isolated_db() as db:
        line = "Nitenpyram, 6-Dose|ldeal for Dogs 2..."
        engine = FakeOcrEngine(line + "\n")
        provider = FakeProvider(
            search_results={
                line: (
                    _candidate(
                        "111111111",
                        "PETARMOR CAPACTION Fast-Acting Oral Flea "
                        "Treatment for Medium and Large Dogs",
                        31.49,
                    ),
                    _candidate(
                        "222222222",
                        "CAPSTAR (Nitenpyram) Fast-Acting Oral Flea "
                        "Treatment for Large Dogs (over 25 lbs), "
                        "6 Tablets, 57 mg",
                        43.18,
                    ),
                )
            }
        )
        cog, verify, _ = _cog(monkeypatch, db, provider=provider, engine=engine)
        message = FakeMessage("", attachments=[FakeAttachment(b"png-bytes")])
        await cog.on_message(message)
        assert verify.calls == ["222222222"]
        rows = await list_dm_list_items(db, USER_ID)
        assert [row.item_id for row in rows] == ["222222222"]
        assert rows[0].status == STATUS_EXACT
