"""DM list pending-read recovery: a killed settle must not hang a reply.

The 2026-11-21 crash loop: the bot process was OOM-killed mid-read,
the in-memory settle task died with it, and the user's reply sat on
"Reading your picture…" forever even though the reader later
finished the job. The contract under test:

* every deferred read records a pending-read row before its settle
  task starts, and a completed settle marks the row settled;
* a recovery pass re-runs the settle for a stale unsettled row —
  the reply is edited into the final list and the row settles;
* a finished reader job nobody consumed (its waiter died) is
  settled from its stored text and consumed, so its items still
  land on the user's list;
* fresh rows and rows with a live settle in this process are never
  touched by a recovery pass.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
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
from sniperplug.services.dm_list_ocr import UnavailableOcrEngine
from sniperplug.services.dm_lists import STATUS_EXACT, list_dm_list_items
from sniperplug.services.dm_ocr_jobs import (
    claim_next_dm_ocr_job,
    finish_dm_ocr_job,
    get_dm_ocr_job,
    record_pending_picture_read,
    submit_dm_ocr_job,
)
from sniperplug.services.walmart_exact_verification_queue import (
    SingleExactVerification,
)

USER_ID = 111
BOT_ID = 999
CHANNEL_ID = 4242
MESSAGE_ID = 555
REPLY_ID = 556
IPHONE_ID = "425031866"
CDN_URL = "https://cdn.discordapp.com/attachments/1/2/cart.png?ex=abc"
CART_OCR_TEXT = "Apple iPhone 11, 64GB, Green -\nUnlocked\n$298.00\nQty 1\n"


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


class FakeSentMessage:
    def __init__(self) -> None:
        self.id = REPLY_ID
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        return self


class FakeChannel:
    def __init__(self, messages=None) -> None:
        self.id = CHANNEL_ID
        self.type = discord.ChannelType.private
        self._messages = messages or {}

    async def fetch_message(self, message_id):
        found = self._messages[message_id]
        return found


class FakeMessage:
    def __init__(self, content: str = "", *, attachments=()) -> None:
        self.content = content
        self.guild = None
        self.channel = FakeChannel()
        self.author = SimpleNamespace(bot=False, id=USER_ID)
        self.attachments = list(attachments)
        self.id = MESSAGE_ID
        self.replies: list[dict] = []
        self.sent = FakeSentMessage()
        self.channel._messages = {MESSAGE_ID: self, REPLY_ID: self.sent}

    async def reply(self, content=None, **kwargs):
        self.replies.append({"content": content, **kwargs})
        return self.sent


class FakeBot:
    def __init__(self, db=None, channel=None) -> None:
        self.db = db
        self.user = SimpleNamespace(id=BOT_ID)
        self._channel = channel

    async def fetch_channel(self, channel_id):
        assert channel_id == CHANNEL_ID
        return self._channel


class FakeAttachment:
    def __init__(self) -> None:
        self.filename = "cart.png"
        self.content_type = "image/png"
        self.size = 9
        self.url = CDN_URL

    async def read(self) -> bytes:
        return b"png-bytes"


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
    async def __call__(self, db, provider, item_id, *, min_discount):
        return SingleExactVerification(
            item_id=item_id,
            candidate=_candidate(item_id, "Apple iPhone 11, 64GB, Green", 298.0),
            classification="verified_markdown",
            status="verified",
            enqueued=True,
        )


class FakeProvider:
    async def healthcheck(self):
        return SimpleNamespace(status=ProviderStatus.READY)

    async def scan(self, request):
        if "iPhone" in request.query:
            return SimpleNamespace(
                candidates=(
                    _candidate(IPHONE_ID, "Apple iPhone 11, 64GB, Green", 310.0),
                )
            )
        return SimpleNamespace(candidates=())


class FakeRemoteOcr:
    def __init__(self, *, text: str | None = CART_OCR_TEXT) -> None:
        self.text = text
        self.read_calls: list[str] = []

    async def available(self, db) -> bool:
        return True

    async def read_text(self, db, *, user_id, attachment_url):
        self.read_calls.append(attachment_url)
        return self.text


def _cog(monkeypatch, db, *, remote=None, bot=None):
    monkeypatch.setattr(intake, "verify_single_exact_item", VerifyStub())
    provider = FakeProvider()
    monkeypatch.setattr(provider_registry, "get", lambda key: provider)
    cog = DmListIntakeCog(
        bot if bot is not None else FakeBot(db),
        DmListIntakeConfig(enabled=True),
        ocr_engine=UnavailableOcrEngine(),
        remote_ocr_engine=remote if remote is not None else FakeRemoteOcr(),
    )
    return cog


async def _backdate(db, table: str, column: str, row_id: int, seconds: float) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    conn = db.require_conn()
    await conn.execute(
        f"UPDATE {table} SET {column} = ? WHERE id = ?", (stamp, row_id)
    )
    await conn.commit()


async def _unsettled(db):
    return await ocr_jobs.list_unsettled_picture_reads(db, min_age_seconds=0)


# --- the record itself -------------------------------------------------------


@pytest.mark.asyncio
async def test_intake_records_pending_read_and_settle_marks_it(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog = _cog(monkeypatch, db)
        message = FakeMessage("", attachments=[FakeAttachment()])
        await cog.on_message(message)
        rows = await _unsettled(db)
        assert len(rows) == 1
        assert rows[0].channel_id == CHANNEL_ID
        assert rows[0].message_id == MESSAGE_ID
        assert rows[0].reply_id == REPLY_ID
        assert rows[0].picture_indexes == (0,)
        tasks = list(cog._pending_tasks)
        assert tasks
        import asyncio

        await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
        assert await _unsettled(db) == []
        rows = await list_dm_list_items(db, USER_ID)
        assert [row.status for row in rows] == [STATUS_EXACT]


# --- recovery of a stale pending read ----------------------------------------


@pytest.mark.asyncio
async def test_recovery_resettles_a_stale_pending_read(monkeypatch) -> None:
    async with _isolated_db() as db:
        message = FakeMessage("", attachments=[FakeAttachment()])
        bot = FakeBot(db, channel=message.channel)
        cog = _cog(monkeypatch, db, bot=bot)
        pending_id = await record_pending_picture_read(
            db,
            user_id=USER_ID,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
            reply_id=REPLY_ID,
            picture_indexes=[0],
        )
        await _backdate(db, "dm_list_pending_reads", "created_at", pending_id, 400)
        await cog._recover_pending_reads_once()
        assert await _unsettled(db) == []
        rows = await list_dm_list_items(db, USER_ID)
        assert [row.status for row in rows] == [STATUS_EXACT]
        assert len(message.sent.edits) == 1
        final = message.sent.edits[0]["embed"].description
        assert "Reading your picture" not in final
        assert "Apple iPhone 11, 64GB, Green" in final


@pytest.mark.asyncio
async def test_recovery_skips_fresh_and_live_records(monkeypatch) -> None:
    async with _isolated_db() as db:
        message = FakeMessage("", attachments=[FakeAttachment()])
        bot = FakeBot(db, channel=message.channel)
        cog = _cog(monkeypatch, db, bot=bot)
        fresh_id = await record_pending_picture_read(
            db,
            user_id=USER_ID,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
            reply_id=REPLY_ID,
            picture_indexes=[0],
        )
        live_id = await record_pending_picture_read(
            db,
            user_id=USER_ID,
            channel_id=CHANNEL_ID,
            message_id=MESSAGE_ID,
            reply_id=REPLY_ID,
            picture_indexes=[0],
        )
        await _backdate(db, "dm_list_pending_reads", "created_at", live_id, 400)
        cog._live_pending_ids.add(live_id)
        await cog._recover_pending_reads_once()
        remaining = {row.id for row in await _unsettled(db)}
        assert remaining == {fresh_id, live_id}
        assert message.sent.edits == []


# --- orphaned finished jobs ---------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_settles_an_orphaned_finished_job(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog = _cog(monkeypatch, db)
        job_id = await submit_dm_ocr_job(db, USER_ID, CDN_URL)
        claimed = await claim_next_dm_ocr_job(db)
        assert claimed is not None and claimed.id == job_id
        await finish_dm_ocr_job(db, job_id, result_text=CART_OCR_TEXT)
        await _backdate(db, "dm_ocr_jobs", "finished_at", job_id, 900)
        await cog._recover_pending_reads_once()
        rows = await list_dm_list_items(db, USER_ID)
        assert [row.status for row in rows] == [STATUS_EXACT]
        assert rows[0].item_id == IPHONE_ID
        # Consumed: the row is gone, not left for the stale sweep.
        assert await get_dm_ocr_job(db, job_id) is None


@pytest.mark.asyncio
async def test_recovery_leaves_a_fresh_finished_job_alone(monkeypatch) -> None:
    async with _isolated_db() as db:
        cog = _cog(monkeypatch, db)
        job_id = await submit_dm_ocr_job(db, USER_ID, CDN_URL)
        claimed = await claim_next_dm_ocr_job(db)
        assert claimed is not None and claimed.id == job_id
        await finish_dm_ocr_job(db, job_id, result_text=CART_OCR_TEXT)
        await cog._recover_pending_reads_once()
        assert await get_dm_ocr_job(db, job_id) is not None
        assert await list_dm_list_items(db, USER_ID) == ()
