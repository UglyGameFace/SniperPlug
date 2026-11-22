"""DM OCR job queue: claim/lease, finish/fail, heartbeat, cleanup.

Fixture-based only — a real in-memory SQLite connection wrapped the
way the DM list tests wrap theirs. These are the shared-table rules
the bot and the standalone reader app both depend on: a picture URL
never outlives its read, a crashed claim returns to the queue, and
the heartbeat's freshness is the reader's on/off switch.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

import sniperplug.services.dm_ocr_jobs as jobs
from sniperplug.services.dm_list_ocr import RemoteOcrEngine
from sniperplug.services.dm_ocr_jobs import (
    JOB_CLAIMED,
    JOB_DONE,
    JOB_FAILED,
    JOB_QUEUED,
    claim_next_dm_ocr_job,
    consume_dm_ocr_job,
    delete_stale_dm_ocr_jobs,
    fail_dm_ocr_job,
    finish_dm_ocr_job,
    get_dm_ocr_job,
    reader_heartbeat_fresh,
    submit_dm_ocr_job,
    touch_reader_heartbeat,
)

USER_ID = 111
URL = "https://cdn.discordapp.com/attachments/1/2/cart.png?ex=abc"
NOW = datetime(2026, 11, 19, 17, 0, tzinfo=timezone.utc)


class _FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


@contextlib.asynccontextmanager
async def _isolated_db():
    jobs._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    try:
        yield _FakeDatabase(conn)
    finally:
        await conn.close()
        jobs._SCHEMA_READY = False


# --- submit / claim -------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_then_claim_oldest_first() -> None:
    async with _isolated_db() as db:
        first = await submit_dm_ocr_job(db, USER_ID, URL + "1")
        second = await submit_dm_ocr_job(db, USER_ID, URL + "2")
        claimed = await claim_next_dm_ocr_job(db, now=NOW)
        assert claimed is not None
        assert claimed.id == first
        assert claimed.status == JOB_CLAIMED
        assert claimed.attachment_url == URL + "1"
        assert claimed.claimed_at == NOW.isoformat()
        claimed2 = await claim_next_dm_ocr_job(db, now=NOW)
        assert claimed2 is not None and claimed2.id == second
        assert await claim_next_dm_ocr_job(db, now=NOW) is None
        stored = await get_dm_ocr_job(db, first)
        assert stored is not None and stored.status == JOB_CLAIMED


@pytest.mark.asyncio
async def test_expired_claim_returns_to_the_queue() -> None:
    async with _isolated_db() as db:
        job_id = await submit_dm_ocr_job(db, USER_ID, URL)
        claimed = await claim_next_dm_ocr_job(db, lease_seconds=180, now=NOW)
        assert claimed is not None and claimed.id == job_id
        # Still inside the lease: nobody else can take it.
        inside = await claim_next_dm_ocr_job(
            db, lease_seconds=180, now=NOW + timedelta(seconds=179)
        )
        assert inside is None
        # Past the lease, the crashed claim is reclaimed.
        reclaimed = await claim_next_dm_ocr_job(
            db, lease_seconds=180, now=NOW + timedelta(seconds=181)
        )
        assert reclaimed is not None and reclaimed.id == job_id
        assert reclaimed.status == JOB_CLAIMED


# --- finish / fail ----------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_writes_text_and_clears_the_url() -> None:
    async with _isolated_db() as db:
        job_id = await submit_dm_ocr_job(db, USER_ID, URL)
        await claim_next_dm_ocr_job(db, now=NOW)
        assert await finish_dm_ocr_job(db, job_id, result_text="Ninja Air Fryer")
        stored = await get_dm_ocr_job(db, job_id)
        assert stored is not None
        assert stored.status == JOB_DONE
        assert stored.result_text == "Ninja Air Fryer"
        assert stored.attachment_url is None  # the URL dies with the read
        assert stored.finished_at


@pytest.mark.asyncio
async def test_fail_marks_failed_with_a_short_error_and_clears_the_url() -> None:
    async with _isolated_db() as db:
        job_id = await submit_dm_ocr_job(db, USER_ID, URL)
        await claim_next_dm_ocr_job(db, now=NOW)
        assert await fail_dm_ocr_job(db, job_id, error="RuntimeError: boom")
        stored = await get_dm_ocr_job(db, job_id)
        assert stored is not None
        assert stored.status == JOB_FAILED
        assert stored.error == "RuntimeError: boom"
        assert stored.attachment_url is None


@pytest.mark.asyncio
async def test_consume_reads_then_deletes_the_row() -> None:
    async with _isolated_db() as db:
        job_id = await submit_dm_ocr_job(db, USER_ID, URL)
        await claim_next_dm_ocr_job(db, now=NOW)
        await finish_dm_ocr_job(db, job_id, result_text="text")
        consumed = await consume_dm_ocr_job(db, job_id)
        assert consumed is not None and consumed.result_text == "text"
        assert await get_dm_ocr_job(db, job_id) is None
        assert await consume_dm_ocr_job(db, job_id) is None


# --- stale sweep ------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_sweep_deletes_only_old_rows() -> None:
    async with _isolated_db() as db:
        old_id = await submit_dm_ocr_job(db, USER_ID, URL + "old")
        # Age the row past the sweep horizon directly in storage.
        conn = db.require_conn()
        await conn.execute(
            "UPDATE dm_ocr_jobs SET created_at = ? WHERE id = ?",
            ((NOW - timedelta(hours=2)).isoformat(), old_id),
        )
        await conn.commit()
        fresh_id = await submit_dm_ocr_job(db, USER_ID, URL + "new")
        swept = await delete_stale_dm_ocr_jobs(db, max_age_seconds=3600, now=NOW)
        assert swept == 1
        assert await get_dm_ocr_job(db, old_id) is None
        kept = await get_dm_ocr_job(db, fresh_id)
        assert kept is not None and kept.status == JOB_QUEUED


# --- heartbeat ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_freshness_window() -> None:
    async with _isolated_db() as db:
        assert await reader_heartbeat_fresh(db, now=NOW) is False  # no row yet
        await touch_reader_heartbeat(db, rss_mb=210.5, now=NOW)
        assert await reader_heartbeat_fresh(db, max_age_seconds=90, now=NOW) is True
        assert (
            await reader_heartbeat_fresh(
                db, max_age_seconds=90, now=NOW + timedelta(seconds=89)
            )
            is True
        )
        assert (
            await reader_heartbeat_fresh(
                db, max_age_seconds=90, now=NOW + timedelta(seconds=91)
            )
            is False
        )
        heartbeat = await jobs.get_reader_heartbeat(db)
        assert heartbeat is not None and heartbeat.rss_mb == 210.5


# --- the bot-side client --------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_engine_available_follows_the_heartbeat() -> None:
    async with _isolated_db() as db:
        engine = RemoteOcrEngine()
        assert await engine.available(db) is False
        await touch_reader_heartbeat(db)  # real now: fresh
        assert await engine.available(db) is True


@pytest.mark.asyncio
async def test_remote_engine_read_text_round_trip() -> None:
    async with _isolated_db() as db:
        await touch_reader_heartbeat(db)
        engine = RemoteOcrEngine(poll_seconds=0.02, timeout_seconds=10)

        async def fake_reader() -> None:
            for _ in range(200):
                job = await claim_next_dm_ocr_job(db)
                if job is not None:
                    await finish_dm_ocr_job(db, job.id, result_text="raw ocr text")
                    return
                await asyncio.sleep(0.01)

        reader = asyncio.create_task(fake_reader())
        text = await engine.read_text(db, user_id=USER_ID, attachment_url=URL)
        await reader
        assert text == "raw ocr text"


@pytest.mark.asyncio
async def test_remote_engine_failed_job_reads_as_none_and_is_consumed() -> None:
    async with _isolated_db() as db:
        await touch_reader_heartbeat(db)
        engine = RemoteOcrEngine(poll_seconds=0.02, timeout_seconds=10)
        seen: list[int] = []

        async def fake_reader() -> None:
            for _ in range(200):
                job = await claim_next_dm_ocr_job(db)
                if job is not None:
                    seen.append(job.id)
                    await fail_dm_ocr_job(db, job.id, error="boom")
                    return
                await asyncio.sleep(0.01)

        reader = asyncio.create_task(fake_reader())
        assert await engine.read_text(db, user_id=USER_ID, attachment_url=URL) is None
        await reader
        assert seen and await get_dm_ocr_job(db, seen[0]) is None


@pytest.mark.asyncio
async def test_remote_engine_times_out_when_nobody_reads() -> None:
    async with _isolated_db() as db:
        await touch_reader_heartbeat(db)
        engine = RemoteOcrEngine(poll_seconds=0.02, timeout_seconds=0.3)
        loop = asyncio.get_running_loop()
        started = loop.time()
        assert await engine.read_text(db, user_id=USER_ID, attachment_url=URL) is None
        assert loop.time() - started < 5  # settled on the timeout, not a hang


@pytest.mark.asyncio
async def test_remote_engine_gives_up_when_the_heartbeat_dies() -> None:
    async with _isolated_db() as db:
        await touch_reader_heartbeat(db, now=NOW - timedelta(seconds=1))
        engine = RemoteOcrEngine(
            poll_seconds=0.02, timeout_seconds=30, heartbeat_max_age_seconds=0.2
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        assert await engine.read_text(db, user_id=USER_ID, attachment_url=URL) is None
        # Settled on the stale heartbeat, far before the 30 s timeout.
        assert loop.time() - started < 5


class _NoLastrowidCursor:
    """The production libsql cursor's shape: rows, but no lastrowid."""

    def __init__(self, cursor):
        self._cursor = cursor

    async def fetchone(self):
        return await self._cursor.fetchone()

    async def fetchall(self):
        return await self._cursor.fetchall()

    def __getattr__(self, name):
        if name == "lastrowid":
            raise AttributeError("production cursor has no lastrowid")
        return getattr(self._cursor, name)


class _NoLastrowidConn:
    def __init__(self, conn):
        self._conn = conn

    async def execute(self, *args, **kwargs):
        return _NoLastrowidCursor(await self._conn.execute(*args, **kwargs))

    async def commit(self):
        await self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.mark.asyncio
async def test_submit_returns_a_usable_id_with_a_production_shaped_cursor() -> None:
    """Regression (2026-11-19): submit read cursor.lastrowid, which
    the production libsql cursor does not expose. The insert
    committed but the call raised, so the bot believed the queue
    had failed and never polled the job it had just created."""

    jobs._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    try:
        db = _FakeDatabase(_NoLastrowidConn(conn))
        job_id = await submit_dm_ocr_job(db, USER_ID, URL)
        assert job_id > 0
        job = await get_dm_ocr_job(db, job_id)
        assert job is not None
        assert job.id == job_id
        assert job.status == JOB_QUEUED
    finally:
        await conn.close()
        jobs._SCHEMA_READY = False


# --- pending picture reads: crash recovery records -------------------------


@pytest.mark.asyncio
async def test_pending_read_round_trip_and_settle() -> None:
    async with _isolated_db() as db:
        pending_id = await jobs.record_pending_picture_read(
            db,
            user_id=USER_ID,
            channel_id=777,
            message_id=555,
            reply_id=556,
            picture_indexes=[0, 2],
        )
        assert pending_id > 0
        real_now = datetime.now(timezone.utc)
        rows = await jobs.list_unsettled_picture_reads(db, now=real_now + timedelta(hours=1))
        assert len(rows) == 1
        row = rows[0]
        assert row.id == pending_id
        assert row.user_id == USER_ID
        assert row.channel_id == 777
        assert row.message_id == 555
        assert row.reply_id == 556
        assert row.picture_indexes == (0, 2)
        assert row.settled_at is None
        assert await jobs.mark_pending_picture_read_settled(db, pending_id) is True
        assert await jobs.list_unsettled_picture_reads(db, now=real_now + timedelta(hours=1)) == []
        # Marking twice is a no-op, not an error.
        assert await jobs.mark_pending_picture_read_settled(db, pending_id) is True


@pytest.mark.asyncio
async def test_pending_read_age_floor_hides_fresh_records() -> None:
    async with _isolated_db() as db:
        await jobs.record_pending_picture_read(
            db, user_id=USER_ID, channel_id=1, message_id=2, reply_id=3,
            picture_indexes=[0],
        )
        # Just recorded: invisible to a recovery pass with a floor.
        assert await jobs.list_unsettled_picture_reads(
            db, min_age_seconds=150, now=datetime.now(timezone.utc)
        ) == []
        later = datetime.now(timezone.utc) + timedelta(seconds=200)
        rows = await jobs.list_unsettled_picture_reads(db, min_age_seconds=150, now=later)
        assert len(rows) == 1


# --- orphaned finished jobs -------------------------------------------------


@pytest.mark.asyncio
async def test_orphaned_finished_jobs_only_old_and_unconsumed() -> None:
    async with _isolated_db() as db:
        real_now = datetime.now(timezone.utc)
        done_id = await submit_dm_ocr_job(db, USER_ID, URL)
        claimed = await claim_next_dm_ocr_job(db, now=real_now)
        assert claimed is not None and claimed.id == done_id
        await finish_dm_ocr_job(db, done_id, result_text="some text")
        failed_id = await submit_dm_ocr_job(db, USER_ID, URL)
        claimed = await claim_next_dm_ocr_job(db, now=real_now)
        assert claimed is not None and claimed.id == failed_id
        await fail_dm_ocr_job(db, failed_id, error="boom")
        queued_id = await submit_dm_ocr_job(db, USER_ID, URL)

        # Finished seconds ago: a live waiter may still consume them.
        assert await jobs.list_orphaned_finished_jobs(db, min_age_seconds=600, now=real_now + timedelta(seconds=60)) == []
        orphans = await jobs.list_orphaned_finished_jobs(
            db, min_age_seconds=600, now=real_now + timedelta(seconds=700)
        )
        assert [job.id for job in orphans] == [done_id, failed_id]
        assert orphans[0].result_text == "some text"
        # The still-queued job is never an orphan.
        assert all(job.id != queued_id for job in orphans)
