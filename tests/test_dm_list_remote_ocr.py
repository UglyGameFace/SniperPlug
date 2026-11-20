"""DM list intake × the remote reader: gating, settle, budgets.

Fixture-based only — no Discord connection, no live Walmart calls,
no real reader app. The remote engine is faked at the cog's
constructor seam (and, in two tests, the real ``RemoteOcrEngine``
runs against the real job queue with a test task playing the
reader). The contract under test:

* heartbeat fresh → the picture is handed over by URL (never
  downloaded by the bot), the reply goes out at once with a
  "Reading your picture…" note, and the settled read edits that
  same reply into the final list;
* heartbeat stale/absent → intake is byte-for-byte the pre-reader
  behavior (unreadable row, "isn't switched on yet", no jobs);
* a failed / timed-out / lost read settles exactly like the
  no-engine path;
* the per-message verify/search budgets are shared across the
  immediate links and the background picture read.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import aiosqlite
import discord
import pytest

import sniperplug.cogs.dm_list_intake as intake
import sniperplug.services.dm_deal_alerts as alerts
import sniperplug.services.dm_lists as dm_lists
import sniperplug.services.dm_ocr_jobs as ocr_jobs
import sniperplug.services.user_wishlist as wishlist
from sniperplug.cogs.dm_list_intake import DmListIntakeCog, DmListIntakeConfig
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.dm_list_ocr import RemoteOcrEngine, UnavailableOcrEngine
from sniperplug.services.dm_lists import (
    STATUS_CLOSEST,
    STATUS_EXACT,
    STATUS_UNREADABLE,
    list_dm_list_items,
)
from sniperplug.services.dm_ocr_jobs import (
    JOB_QUEUED,
    claim_next_dm_ocr_job,
    fail_dm_ocr_job,
    get_dm_ocr_job,
    touch_reader_heartbeat,
)
from sniperplug.services.walmart_exact_verification_queue import (
    SingleExactVerification,
)

USER_ID = 111
BOT_ID = 999
IPHONE_URL = "https://www.walmart.com/ip/Apple-iPhone-11-AT-T-64GB-Green/425031866"
IPHONE_ID = "425031866"
NINJA_ID = "987654321"
CDN_URL = "https://cdn.discordapp.com/attachments/1/2/cart.png?ex=abc"

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


# --- fakes --------------------------------------------------------------------


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
    ocr_jobs._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    try:
        yield _FakeDatabase(conn)
    finally:
        await conn.close()
        dm_lists._SCHEMA_READY = False
        wishlist._SCHEMA_READY = False
        alerts._SCHEMA_READY = False
        ocr_jobs._SCHEMA_READY = False


class FakeBot:
    def __init__(self, db=None) -> None:
        self.db = db
        self.user = SimpleNamespace(id=BOT_ID)


class FakeSentMessage:
    def __init__(self) -> None:
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        return self


class FakeMessage:
    def __init__(
        self,
        content: str = "",
        *,
        attachments=(),
        message_id: int = 555,
    ) -> None:
        self.content = content
        self.guild = None
        self.channel = SimpleNamespace(type=discord.ChannelType.private)
        self.author = SimpleNamespace(bot=False, id=USER_ID)
        self.attachments = list(attachments)
        self.id = message_id
        self.replies: list[dict] = []
        self.sent = FakeSentMessage()

    async def reply(self, content=None, **kwargs):
        self.replies.append({"content": content, **kwargs})
        return self.sent


class FakeAttachment:
    def __init__(
        self,
        data: bytes = b"png-bytes",
        *,
        size: int | None = None,
        url: str = CDN_URL,
    ) -> None:
        self._data = data
        self.filename = "cart.png"
        self.content_type = "image/png"
        self.size = len(data) if size is None else size
        self.url = url
        self.read_calls = 0

    async def read(self) -> bytes:
        self.read_calls += 1
        return self._data


def _candidate(item_id: str, title: str, price: float):
    return SourceCandidate(
        source_key="walmart",
        retailer="walmart",
        title=title,
        product_url=f"https://www.walmart.com/ip/{item_id}",
        api_current_price=price,
        api_reference_price=price * 2,
    )


class VerifyStub:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, db, provider, item_id, *, min_discount):
        self.calls.append(item_id)
        titles = {
            IPHONE_ID: "Apple iPhone 11, 64GB, Green",
            NINJA_ID: "Ninja AF101 Air Fryer, 4 Quart",
        }
        return SingleExactVerification(
            item_id=item_id,
            candidate=_candidate(
                item_id, titles.get(item_id, f"Product {item_id}"), 298.0
            ),
            classification="verified_markdown",
            status="verified",
            enqueued=True,
        )


class FakeProvider:
    def __init__(self, search_results=None) -> None:
        self.search_results = search_results or {}
        self.queries: list[str] = []

    async def healthcheck(self):
        return SimpleNamespace(status=ProviderStatus.READY)

    async def scan(self, request):
        self.queries.append(request.query)
        return SimpleNamespace(
            candidates=tuple(self.search_results.get(request.query, ()))
        )


class FakeRemoteOcr:
    """The cog's remote seam: scripted availability + read outcome."""

    def __init__(self, *, available: bool = True, text: str | None = None) -> None:
        self._available = available
        self.text = text
        self.read_calls: list[str] = []

    async def available(self, db) -> bool:
        return self._available

    async def read_text(self, db, *, user_id, attachment_url):
        self.read_calls.append(attachment_url)
        return self.text


def _cart_provider() -> FakeProvider:
    return FakeProvider(
        search_results={
            "Apple iPhone 11, 64GB, Green": (
                _candidate(IPHONE_ID, "Apple iPhone 11, 64GB, Green", 310.0),
            ),
            "Ninja AF101 Air Fryer, 4 Quart": (
                _candidate(NINJA_ID, "Ninja AF101 Air Fryer, 4 Quart", 95.0),
            ),
        }
    )


def _cog(monkeypatch, db, *, remote, provider=None, verify=None):
    verify = verify if verify is not None else VerifyStub()
    provider = provider if provider is not None else _cart_provider()
    monkeypatch.setattr(intake, "verify_single_exact_item", verify)
    monkeypatch.setattr(provider_registry, "get", lambda key: provider)
    cog = DmListIntakeCog(
        FakeBot(db),
        DmListIntakeConfig(enabled=True),
        ocr_engine=UnavailableOcrEngine(),
        remote_ocr_engine=remote,
    )
    return cog, verify, provider


async def _settle(cog) -> None:
    tasks = list(cog._pending_tasks)
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)


# --- the happy path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_picture_is_read_remotely_and_edits_the_reply(monkeypatch) -> None:
    async with _isolated_db() as db:
        remote = FakeRemoteOcr(text=CART_OCR_TEXT)
        cog, verify, provider = _cog(monkeypatch, db, remote=remote)
        attachment = FakeAttachment()
        message = FakeMessage("", attachments=[attachment])
        await cog.on_message(message)
        # The reply went out at once, before the read settled.
        assert len(message.replies) == 1
        first = message.replies[0]["embed"].description
        assert "Reading your picture" in first
        assert remote.read_calls == []  # the background task has not run yet
        await _settle(cog)
        # Handed over by URL; the bot never downloaded the picture.
        assert remote.read_calls == [CDN_URL]
        assert attachment.read_calls == 0
        assert provider.queries == [
            "Apple iPhone 11, 64GB, Green",
            "Ninja AF101 Air Fryer, 4 Quart",
        ]
        assert verify.calls == [IPHONE_ID, NINJA_ID]
        rows = await list_dm_list_items(db, USER_ID)
        assert [row.status for row in rows] == [STATUS_EXACT, STATUS_EXACT]
        assert rows[0].price_cents == 29800
        # The same reply was edited into the final list, exactly once.
        assert len(message.sent.edits) == 1
        final = message.sent.edits[0]["embed"].description
        assert "Reading your picture" not in final
        assert "Apple iPhone 11, 64GB, Green" in final
        assert "exact match" in final


@pytest.mark.asyncio
async def test_links_reply_at_once_and_the_picture_settles_into_it(
    monkeypatch,
) -> None:
    async with _isolated_db() as db:
        remote = FakeRemoteOcr(text="Ninja AF101 Air Fryer, 4 Quart\n$89.99\n")
        cog, verify, _ = _cog(monkeypatch, db, remote=remote)
        message = FakeMessage(IPHONE_URL, attachments=[FakeAttachment()])
        await cog.on_message(message)
        first = message.replies[0]["embed"].description
        assert "Apple iPhone 11, 64GB, Green" in first  # the link, already
        assert "Reading your picture" in first
        await _settle(cog)
        assert verify.calls == [IPHONE_ID, NINJA_ID]
        rows = await list_dm_list_items(db, USER_ID)
        assert [row.status for row in rows] == [STATUS_EXACT, STATUS_EXACT]
        assert len(message.replies) == 1  # one reply, edited — not two messages
        final = message.sent.edits[0]["embed"].description
        assert "Ninja AF101 Air Fryer, 4 Quart" in final


# --- gating: reader down means the old behavior, untouched -----------------------


@pytest.mark.asyncio
async def test_reader_down_keeps_the_pre_reader_behavior(monkeypatch) -> None:
    async with _isolated_db() as db:
        remote = FakeRemoteOcr(available=False)
        cog, verify, _ = _cog(monkeypatch, db, remote=remote)
        attachment = FakeAttachment()
        message = FakeMessage("", attachments=[attachment])
        await cog.on_message(message)
        assert cog._pending_tasks == set()
        assert remote.read_calls == []
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1 and rows[0].status == STATUS_UNREADABLE
        description = message.replies[0]["embed"].description
        assert "couldn't read" in description
        assert "isn't switched on yet" in description
        assert message.sent.edits == []
        assert verify.calls == []


@pytest.mark.asyncio
async def test_no_heartbeat_row_means_the_reader_is_down(monkeypatch) -> None:
    async with _isolated_db() as db:
        # The real client against the real queue: no heartbeat row was
        # ever written, so availability is honestly False.
        cog, _, _ = _cog(monkeypatch, db, remote=RemoteOcrEngine())
        message = FakeMessage("", attachments=[FakeAttachment()])
        await cog.on_message(message)
        assert cog._pending_tasks == set()
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1 and rows[0].status == STATUS_UNREADABLE
        assert "isn't switched on yet" in message.replies[0]["embed"].description


# --- failure families settle like the no-engine path ------------------------------


@pytest.mark.asyncio
async def test_failed_read_settles_unreadable_with_the_plain_note(
    monkeypatch,
) -> None:
    async with _isolated_db() as db:
        remote = FakeRemoteOcr(text=None)  # the read failed / timed out
        cog, _, _ = _cog(monkeypatch, db, remote=remote)
        message = FakeMessage("", attachments=[FakeAttachment()])
        await cog.on_message(message)
        await _settle(cog)
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1 and rows[0].status == STATUS_UNREADABLE
        assert len(message.sent.edits) == 1
        final = message.sent.edits[0]["embed"].description
        assert "couldn't read" in final
        assert "isn't switched on yet" in final


@pytest.mark.asyncio
async def test_real_client_timeout_settles_unreadable(monkeypatch) -> None:
    async with _isolated_db() as db:
        await touch_reader_heartbeat(db)  # reader "up", but never claims
        remote = RemoteOcrEngine(poll_seconds=0.02, timeout_seconds=0.3)
        cog, _, _ = _cog(monkeypatch, db, remote=remote)
        message = FakeMessage("", attachments=[FakeAttachment()])
        await cog.on_message(message)
        await _settle(cog)
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1 and rows[0].status == STATUS_UNREADABLE
        final = message.sent.edits[0]["embed"].description
        assert "isn't switched on yet" in final
        # The unclaimed job is left for the reader's stale sweep, URL
        # and all — the bot does not delete a job it never consumed.
        conn = db.require_conn()
        cursor = await conn.execute("SELECT status FROM dm_ocr_jobs")
        stored = await cursor.fetchall()
        assert [row[0] for row in stored] == [JOB_QUEUED]


@pytest.mark.asyncio
async def test_real_client_failed_job_settles_unreadable_and_consumes(
    monkeypatch,
) -> None:
    async with _isolated_db() as db:
        await touch_reader_heartbeat(db)
        remote = RemoteOcrEngine(poll_seconds=0.02, timeout_seconds=10)
        cog, _, _ = _cog(monkeypatch, db, remote=remote)

        async def fake_reader() -> None:
            for _ in range(250):
                job = await claim_next_dm_ocr_job(db)
                if job is not None:
                    await fail_dm_ocr_job(db, job.id, error="engine exploded")
                    return
                await asyncio.sleep(0.01)

        reader = asyncio.create_task(fake_reader())
        message = FakeMessage("", attachments=[FakeAttachment()])
        await cog.on_message(message)
        await _settle(cog)
        await reader
        rows = await list_dm_list_items(db, USER_ID)
        assert len(rows) == 1 and rows[0].status == STATUS_UNREADABLE
        assert "isn't switched on yet" in message.sent.edits[0]["embed"].description
        conn = db.require_conn()
        cursor = await conn.execute("SELECT COUNT(*) FROM dm_ocr_jobs")
        assert (await cursor.fetchone())[0] == 0  # consumed, not left behind


# --- budgets + size rules still hold on the remote path ---------------------------


@pytest.mark.asyncio
async def test_verify_budget_is_shared_with_the_background_read(
    monkeypatch,
) -> None:
    async with _isolated_db() as db:
        remote = FakeRemoteOcr(text="Ninja AF101 Air Fryer, 4 Quart\n$89.99\n")
        cog, verify, _ = _cog(monkeypatch, db, remote=remote)
        ids = [str(100000000 + n) for n in range(10)]
        content = " ".join(f"https://www.walmart.com/ip/{item_id}" for item_id in ids)
        message = FakeMessage(content, attachments=[FakeAttachment()])
        await cog.on_message(message)
        await _settle(cog)
        # All ten verifications went to the links; the picture's hit
        # could only be a closest match — the cap is per message, not
        # per phase.
        assert len(verify.calls) == intake.MAX_VERIFICATIONS_PER_MESSAGE
        rows = await list_dm_list_items(db, USER_ID)
        statuses = [row.status for row in rows]
        assert statuses.count(STATUS_EXACT) == 10
        assert statuses.count(STATUS_CLOSEST) == 1
        closest = [row for row in rows if row.status == STATUS_CLOSEST][0]
        assert closest.price_cents is None
        final = message.sent.edits[0]["embed"].description
        assert "first 10" in final


@pytest.mark.asyncio
async def test_oversize_picture_with_reader_up_is_noted_not_deferred(
    monkeypatch,
) -> None:
    async with _isolated_db() as db:
        remote = FakeRemoteOcr(text=CART_OCR_TEXT)
        cog, _, _ = _cog(monkeypatch, db, remote=remote)
        attachment = FakeAttachment(size=intake.MAX_ATTACHMENT_BYTES + 1)
        message = FakeMessage("", attachments=[attachment])
        await cog.on_message(message)
        assert cog._pending_tasks == set()
        assert remote.read_calls == []
        assert await list_dm_list_items(db, USER_ID) == ()
        assert "too big" in message.replies[0]["content"]


@pytest.mark.asyncio
async def test_settle_failure_replaces_the_reading_note(monkeypatch) -> None:
    """If the settle itself fails, the reply must not sit on
    "Reading your picture…" forever (2026-11-19 production bug):
    it is replaced with a plain failure note."""

    async with _isolated_db() as db:
        remote = FakeRemoteOcr(text=CART_OCR_TEXT)
        cog, _verify, _provider = _cog(monkeypatch, db, remote=remote)
        real_add = intake.add_dm_list_items
        calls = {"n": 0}

        async def flaky_add(db_, user_id, candidates):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("save exploded")
            return await real_add(db_, user_id, candidates)

        monkeypatch.setattr(intake, "add_dm_list_items", flaky_add)
        message = FakeMessage("", attachments=[FakeAttachment()])
        await cog.on_message(message)
        assert len(message.replies) == 1
        await _settle(cog)
        assert calls["n"] == 2
        assert len(message.sent.edits) == 1
        edit = message.sent.edits[0]
        assert edit["content"] == intake.SETTLE_FAILURE_NOTE
        assert edit["embed"] is None
        assert edit["view"] is None
