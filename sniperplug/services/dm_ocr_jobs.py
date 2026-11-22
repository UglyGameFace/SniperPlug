"""DM picture-reading jobs: the queue between the bot and the reader.

Picture reading for DM list intake runs in a separate small Discloud
app (``SniperPlug-Reader``, see ``sniperplug.ocr_reader``) because the
measured OCR engine cannot fit the bot's own 256 MB container (gate
notes in ``sniperplug.services.dm_list_ocr``). The two apps never talk
directly; they share only the production Turso database, exactly like
the HP watcher does. This module is the whole contract:

* ``dm_ocr_jobs`` — one row per picture to read. The bot inserts a
  ``queued`` row carrying the Discord attachment URL; the reader
  claims it (``claimed``, with a lease so a crashed claim returns to
  the queue), downloads the picture itself, and finishes the row with
  the raw OCR text (``done``) or a short error (``failed``). Finishing
  a row **clears the attachment URL** — no picture URL outlives its
  read. The bot deletes the row once it has consumed the result, and
  the reader sweeps any row older than an hour, so URLs are bounded
  in lifetime even if the bot dies mid-read.
* ``dm_ocr_reader_heartbeat`` — a single row the reader refreshes
  every ~30 s with its timestamp and current memory use. The bot
  treats a heartbeat younger than 90 s as "the reader is up" — that
  freshness check is the feature's on/off switch, so no env flag on
  the bot is involved (Discloud env changes do not apply on restart;
  a stale heartbeat turns picture reading off by itself).
* ``dm_list_pending_reads`` — one row per DM reply left sitting on
  "Reading your picture…" while a background task settles its
  pictures. The row carries everything needed to find that reply
  again (channel / message / reply ids plus the picture indexes),
  because the settling task itself is in-memory: when the process
  died mid-read on 2026-11-21 (an OOM kill — see the reader's
  memory-limit tripwire), the task died with it and the reply hung
  forever. The intake cog's recovery pass re-runs the settle for
  any row still unsettled past the waiter's own timeout, so a
  killed read always resolves — or fails honestly — after a
  restart instead of hanging.

Storage follows the per-user pattern from ``dm_lists``: ``CREATE
TABLE IF NOT EXISTS`` behind a process-wide ready flag, plus a
one-time marker row in ``sniperplug_data_migrations``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

JOBS_TABLE = "dm_ocr_jobs"
HEARTBEAT_TABLE = "dm_ocr_reader_heartbeat"
PENDING_READS_TABLE = "dm_list_pending_reads"
DM_OCR_MIGRATION_KEY = "20261119_dm_ocr_jobs_tables"

JOB_QUEUED = "queued"
JOB_CLAIMED = "claimed"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_STATUSES = frozenset({JOB_QUEUED, JOB_CLAIMED, JOB_DONE, JOB_FAILED})

DEFAULT_JOB_LEASE_SECONDS = 180
DEFAULT_HEARTBEAT_MAX_AGE_SECONDS = 90.0
STALE_JOB_MAX_AGE_SECONDS = 3600

_SCHEMA_READY = False
_SCHEMA_LOCK = asyncio.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_iso(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class DmOcrJob:
    id: int
    user_id: int
    status: str
    attachment_url: str | None = None
    result_text: str | None = None
    error: str | None = None
    created_at: str = ""
    claimed_at: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True)
class DmOcrHeartbeat:
    updated_at: datetime
    rss_mb: float | None = None


@dataclass(frozen=True)
class PendingPictureRead:
    """A DM reply waiting on background picture reads.

    Recorded by the intake cog the moment the "Reading your
    picture…" reply goes out and marked settled when the settle
    finishes — in whichever process finishes it. ``picture_indexes``
    are the indexes (into the original message's image attachments)
    that were deferred to the reader.
    """

    id: int
    user_id: int
    channel_id: int
    message_id: int
    reply_id: int
    picture_indexes: tuple[int, ...] = ()
    created_at: str = ""
    settled_at: str | None = None


async def ensure_dm_ocr_tables(db: Any) -> None:
    """Initialize the jobs + heartbeat tables once per process."""

    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = db.require_conn()
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {JOBS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                attachment_url TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                result_text TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                claimed_at TEXT,
                finished_at TEXT
            )
            """
        )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{JOBS_TABLE}_status "
            f"ON {JOBS_TABLE} (status, created_at)"
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {HEARTBEAT_TABLE} (
                id INTEGER PRIMARY KEY,
                updated_at TEXT NOT NULL,
                rss_mb REAL
            )
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PENDING_READS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                reply_id TEXT NOT NULL,
                picture_indexes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                settled_at TEXT
            )
            """
        )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{PENDING_READS_TABLE}_settled "
            f"ON {PENDING_READS_TABLE} (settled_at, created_at)"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sniperplug_data_migrations (
                migration_key TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            "INSERT INTO sniperplug_data_migrations (migration_key, applied_at) "
            "VALUES (?, ?) ON CONFLICT(migration_key) DO NOTHING",
            (DM_OCR_MIGRATION_KEY, _utc_now_iso()),
        )
        await conn.commit()
        _SCHEMA_READY = True


_JOB_COLUMNS = (
    "id, user_id, attachment_url, status, result_text, error, "
    "created_at, claimed_at, finished_at"
)


def _job_from_db(row: Any) -> DmOcrJob:
    return DmOcrJob(
        id=int(_row_get(row, "id", 0) or 0),
        user_id=int(_row_get(row, "user_id", 1) or 0),
        attachment_url=(
            str(_row_get(row, "attachment_url", 2))
            if _row_get(row, "attachment_url", 2)
            else None
        ),
        status=str(_row_get(row, "status", 3) or ""),
        result_text=(
            str(_row_get(row, "result_text", 4))
            if _row_get(row, "result_text", 4) is not None
            else None
        ),
        error=(
            str(_row_get(row, "error", 5))
            if _row_get(row, "error", 5) is not None
            else None
        ),
        created_at=str(_row_get(row, "created_at", 6) or ""),
        claimed_at=(
            str(_row_get(row, "claimed_at", 7))
            if _row_get(row, "claimed_at", 7)
            else None
        ),
        finished_at=(
            str(_row_get(row, "finished_at", 8))
            if _row_get(row, "finished_at", 8)
            else None
        ),
    )


async def submit_dm_ocr_job(db: Any, user_id: int, attachment_url: str) -> int:
    """Queue one picture for the reader; returns the job id."""

    await ensure_dm_ocr_tables(db)
    conn = db.require_conn()
    # RETURNING, not cursor.lastrowid: the production libsql cursor
    # does not expose lastrowid (only the sqlite test double does) —
    # asking for it crashed the save in production on 2026-11-19,
    # after the insert had already committed. RETURNING is the
    # pattern the rest of the bot already uses on this driver.
    cursor = await conn.execute(
        f"INSERT INTO {JOBS_TABLE} (user_id, attachment_url, status, created_at) "
        f"VALUES (?, ?, ?, ?) RETURNING id",
        (str(int(user_id)), str(attachment_url), JOB_QUEUED, _utc_now_iso()),
    )
    row = await cursor.fetchone()
    await conn.commit()
    return int(_row_get(row, "id", 0) or 0)


async def get_dm_ocr_job(db: Any, job_id: int) -> DmOcrJob | None:
    await ensure_dm_ocr_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT {_JOB_COLUMNS} FROM {JOBS_TABLE} WHERE id = ?", (int(job_id),)
    )
    row = await cursor.fetchone()
    return _job_from_db(row) if row is not None else None


async def claim_next_dm_ocr_job(
    db: Any,
    *,
    lease_seconds: int = DEFAULT_JOB_LEASE_SECONDS,
    now: datetime | None = None,
) -> DmOcrJob | None:
    """Claim the oldest queued job, reclaiming expired claims first.

    A claim older than ``lease_seconds`` is treated as crashed and
    returned to the queue before the oldest queued row is picked, so
    a reader that dies mid-picture never strands the job. The claim
    itself is a guarded update (``status`` must still be ``queued``),
    verified by re-reading the row — safe even if two readers ever
    run at once.
    """

    await ensure_dm_ocr_tables(db)
    moment = now or _utc_now()
    now_iso = moment.isoformat()
    lease_cutoff = (moment - timedelta(seconds=max(1, int(lease_seconds)))).isoformat()
    conn = db.require_conn()
    await conn.execute(
        f"UPDATE {JOBS_TABLE} SET status = ?, claimed_at = NULL "
        f"WHERE status = ? AND claimed_at IS NOT NULL AND claimed_at < ?",
        (JOB_QUEUED, JOB_CLAIMED, lease_cutoff),
    )
    await conn.commit()
    cursor = await conn.execute(
        f"SELECT {_JOB_COLUMNS} FROM {JOBS_TABLE} WHERE status = ? "
        f"ORDER BY created_at ASC, id ASC LIMIT 1",
        (JOB_QUEUED,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    candidate = _job_from_db(row)
    await conn.execute(
        f"UPDATE {JOBS_TABLE} SET status = ?, claimed_at = ? "
        f"WHERE id = ? AND status = ?",
        (JOB_CLAIMED, now_iso, candidate.id, JOB_QUEUED),
    )
    await conn.commit()
    claimed = await get_dm_ocr_job(db, candidate.id)
    if (
        claimed is None
        or claimed.status != JOB_CLAIMED
        or claimed.claimed_at != now_iso
    ):
        return None  # Another reader won the race; the next tick retries.
    return claimed


async def finish_dm_ocr_job(db: Any, job_id: int, *, result_text: str) -> bool:
    """Mark a claimed job done with its raw OCR text; clears the URL."""

    await ensure_dm_ocr_tables(db)
    conn = db.require_conn()
    await conn.execute(
        f"UPDATE {JOBS_TABLE} SET status = ?, result_text = ?, error = NULL, "
        f"attachment_url = NULL, finished_at = ? WHERE id = ? AND status = ?",
        (JOB_DONE, str(result_text or ""), _utc_now_iso(), int(job_id), JOB_CLAIMED),
    )
    await conn.commit()
    job = await get_dm_ocr_job(db, job_id)
    return bool(job is not None and job.status == JOB_DONE)


async def fail_dm_ocr_job(db: Any, job_id: int, *, error: str) -> bool:
    """Mark a claimed job failed with a short error; clears the URL."""

    await ensure_dm_ocr_tables(db)
    conn = db.require_conn()
    await conn.execute(
        f"UPDATE {JOBS_TABLE} SET status = ?, error = ?, attachment_url = NULL, "
        f"finished_at = ? WHERE id = ? AND status = ?",
        (
            JOB_FAILED,
            str(error or "unknown error")[:200],
            _utc_now_iso(),
            int(job_id),
            JOB_CLAIMED,
        ),
    )
    await conn.commit()
    job = await get_dm_ocr_job(db, job_id)
    return bool(job is not None and job.status == JOB_FAILED)


async def consume_dm_ocr_job(db: Any, job_id: int) -> DmOcrJob | None:
    """Read a job and delete its row — the bot's side of the handoff."""

    job = await get_dm_ocr_job(db, job_id)
    if job is None:
        return None
    conn = db.require_conn()
    await conn.execute(f"DELETE FROM {JOBS_TABLE} WHERE id = ?", (int(job_id),))
    await conn.commit()
    return job


async def delete_stale_dm_ocr_jobs(
    db: Any,
    *,
    max_age_seconds: int = STALE_JOB_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> int:
    """Delete every job row older than ``max_age_seconds``; returns the count.

    The reader runs this on its heartbeat tick. It is the backstop
    that bounds how long an attachment URL can sit in the table: a
    job the bot never consumed (it died mid-read) and a job nobody
    ever claimed (the reader was down) both disappear within the
    hour, and Discord's signed attachment URLs have expired by then
    anyway.
    """

    await ensure_dm_ocr_tables(db)
    moment = now or _utc_now()
    cutoff = (moment - timedelta(seconds=max(1, int(max_age_seconds)))).isoformat()
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT COUNT(*) FROM {JOBS_TABLE} WHERE created_at < ?", (cutoff,)
    )
    row = await cursor.fetchone()
    count = int(_row_get(row, "COUNT(*)", 0) or 0) if row is not None else 0
    if count:
        await conn.execute(
            f"DELETE FROM {JOBS_TABLE} WHERE created_at < ?", (cutoff,)
        )
        await conn.commit()
    return count


async def touch_reader_heartbeat(
    db: Any, *, rss_mb: float | None = None, now: datetime | None = None
) -> None:
    """Refresh the reader's single heartbeat row."""

    await ensure_dm_ocr_tables(db)
    moment = now or _utc_now()
    conn = db.require_conn()
    await conn.execute(
        f"INSERT INTO {HEARTBEAT_TABLE} (id, updated_at, rss_mb) VALUES (1, ?, ?) "
        f"ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, "
        f"rss_mb = excluded.rss_mb",
        (moment.isoformat(), rss_mb),
    )
    await conn.commit()


async def get_reader_heartbeat(db: Any) -> DmOcrHeartbeat | None:
    await ensure_dm_ocr_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT updated_at, rss_mb FROM {HEARTBEAT_TABLE} WHERE id = 1"
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    updated_at = _parse_iso(_row_get(row, "updated_at", 0))
    if updated_at is None:
        return None
    rss_raw = _row_get(row, "rss_mb", 1)
    return DmOcrHeartbeat(
        updated_at=updated_at,
        rss_mb=float(rss_raw) if rss_raw is not None else None,
    )


async def reader_heartbeat_fresh(
    db: Any,
    *,
    max_age_seconds: float = DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> bool:
    """True when the reader has checked in within ``max_age_seconds``."""

    heartbeat = await get_reader_heartbeat(db)
    if heartbeat is None:
        return False
    moment = now or _utc_now()
    age = (moment - heartbeat.updated_at).total_seconds()
    return 0 <= age <= float(max_age_seconds)


# -- pending picture reads: crash recovery for the bot's reply -------


async def record_pending_picture_read(
    db: Any,
    *,
    user_id: int,
    channel_id: int,
    message_id: int,
    reply_id: int,
    picture_indexes: Iterable[int] = (),
) -> int:
    """Record one "Reading your picture…" reply for crash recovery."""

    await ensure_dm_ocr_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"INSERT INTO {PENDING_READS_TABLE} "
        f"(user_id, channel_id, message_id, reply_id, picture_indexes, "
        f"created_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        (
            str(int(user_id)),
            str(int(channel_id)),
            str(int(message_id)),
            str(int(reply_id)),
            ",".join(str(int(i)) for i in picture_indexes),
            _utc_now_iso(),
        ),
    )
    row = await cursor.fetchone()
    await conn.commit()
    return int(_row_get(row, "id", 0) or 0)


async def mark_pending_picture_read_settled(db: Any, pending_id: int) -> bool:
    """Mark a pending read settled; False when it already was."""

    await ensure_dm_ocr_tables(db)
    conn = db.require_conn()
    await conn.execute(
        f"UPDATE {PENDING_READS_TABLE} SET settled_at = ? "
        f"WHERE id = ? AND settled_at IS NULL",
        (_utc_now_iso(), int(pending_id)),
    )
    await conn.commit()
    cursor = await conn.execute(
        f"SELECT settled_at FROM {PENDING_READS_TABLE} WHERE id = ?",
        (int(pending_id),),
    )
    row = await cursor.fetchone()
    return bool(row is not None and _row_get(row, "settled_at", 0))


_PENDING_COLUMNS = (
    "id, user_id, channel_id, message_id, reply_id, picture_indexes, "
    "created_at, settled_at"
)


def _pending_from_db(row: Any) -> PendingPictureRead:
    raw_indexes = str(_row_get(row, "picture_indexes", 5) or "")
    indexes = tuple(
        int(part) for part in raw_indexes.split(",") if part.strip().lstrip("-").isdigit()
    )
    settled = _row_get(row, "settled_at", 7)
    return PendingPictureRead(
        id=int(_row_get(row, "id", 0) or 0),
        user_id=int(_row_get(row, "user_id", 1) or 0),
        channel_id=int(_row_get(row, "channel_id", 2) or 0),
        message_id=int(_row_get(row, "message_id", 3) or 0),
        reply_id=int(_row_get(row, "reply_id", 4) or 0),
        picture_indexes=indexes,
        created_at=str(_row_get(row, "created_at", 6) or ""),
        settled_at=str(settled) if settled else None,
    )


async def list_unsettled_picture_reads(
    db: Any,
    *,
    min_age_seconds: float = 0,
    limit: int = 10,
    now: datetime | None = None,
) -> list[PendingPictureRead]:
    """Unsettled pending reads at least ``min_age_seconds`` old.

    The age floor is what keeps a recovery pass from racing a live
    settle: a settle still inside its own wait budget is younger
    than the floor the caller passes (the intake uses a floor past
    the remote waiter's 120 s timeout).
    """

    await ensure_dm_ocr_tables(db)
    moment = now or _utc_now()
    cutoff = (moment - timedelta(seconds=max(0.0, float(min_age_seconds)))).isoformat()
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT {_PENDING_COLUMNS} FROM {PENDING_READS_TABLE} "
        f"WHERE settled_at IS NULL AND created_at <= ? "
        f"ORDER BY created_at ASC, id ASC LIMIT ?",
        (cutoff, max(1, int(limit))),
    )
    return [_pending_from_db(row) for row in await cursor.fetchall()]


async def list_orphaned_finished_jobs(
    db: Any,
    *,
    min_age_seconds: float = 600,
    limit: int = 3,
    now: datetime | None = None,
) -> list[DmOcrJob]:
    """Finished jobs nobody consumed, finished a while ago.

    A live waiter consumes its job within its own 120 s timeout, so
    a done/failed row still present ``min_age_seconds`` after it
    finished has no owner: the process holding the waiter died.
    The intake's recovery pass settles these from the stored result
    (there is no reply reference to edit — the rows predate pending
    records, or their waiter died before recording one) and then
    consumes them, instead of leaving them for the 1 h stale sweep.
    """

    await ensure_dm_ocr_tables(db)
    moment = now or _utc_now()
    cutoff = (moment - timedelta(seconds=max(0.0, float(min_age_seconds)))).isoformat()
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT {_JOB_COLUMNS} FROM {JOBS_TABLE} "
        f"WHERE status IN (?, ?) AND finished_at IS NOT NULL "
        f"AND finished_at <= ? ORDER BY finished_at ASC, id ASC LIMIT ?",
        (JOB_DONE, JOB_FAILED, cutoff, max(1, int(limit))),
    )
    return [_job_from_db(row) for row in await cursor.fetchall()]


def _row_get(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)
