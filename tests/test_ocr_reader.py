"""The standalone picture reader: engine shaping + the job loop.

Fixture-based only — no real rapidocr in the unit suite (it is not
installed for the bot's test environment; the measured real-engine
run lives in the build report). The engine's image shaping is tested
with Pillow when importable; the service is tested end to end
against an in-memory database with a fake engine and a fake
downloader, which is everything except the OCR model itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import io

import aiosqlite
import pytest

import sniperplug.services.dm_ocr_jobs as jobs
from sniperplug.ocr_reader.config import OcrReaderSettings
from sniperplug.ocr_reader.engine import RapidOcrEngine, preprocess_image, strip_boxes
from sniperplug.ocr_reader.service import MAX_JOB_IMAGE_BYTES, OcrReaderService
from sniperplug.services.dm_ocr_jobs import (
    JOB_DONE,
    JOB_FAILED,
    get_dm_ocr_job,
    reader_heartbeat_fresh,
    submit_dm_ocr_job,
    touch_reader_heartbeat,
)

USER_ID = 111
URL = "https://cdn.discordapp.com/attachments/1/2/cart.png?ex=abc"


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


# --- image shaping ----------------------------------------------------------


def test_strip_boxes_cover_the_page_with_overlap() -> None:
    assert strip_boxes(500) == [(0, 500)]
    assert strip_boxes(900) == [(0, 900)]
    assert strip_boxes(901) == [(0, 900), (840, 901)]
    assert strip_boxes(2532) == [(0, 900), (840, 1740), (1680, 2532)]
    assert strip_boxes(0) == []


def test_preprocess_downscales_keeping_aspect_and_grayscales() -> None:
    Image = pytest.importorskip("PIL.Image")
    image = Image.new("RGB", (2000, 3000), "white")
    out = preprocess_image(image)
    assert out.mode == "L"
    assert out.size == (1000, 1500)  # same proportions, capped width
    small = preprocess_image(Image.new("RGB", (800, 600), "white"))
    assert small.size == (800, 600)  # never upscaled
    assert small.mode == "L"


def test_engine_joins_strip_text_and_loads_lazily() -> None:
    Image = pytest.importorskip("PIL.Image")
    calls: list[object] = []

    def fake_callable(strip):
        calls.append(strip)
        return ([[None, "Apple iPhone 11, 64GB, Green", 0.99]], 0.1)

    engine = RapidOcrEngine(engine_factory=lambda: fake_callable)
    assert engine.loaded is False
    buffer = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(buffer, format="PNG")
    text = engine.extract_text(buffer.getvalue())
    assert engine.loaded is True
    assert text == "Apple iPhone 11, 64GB, Green"
    assert len(calls) == 1  # one strip for a short image


def test_engine_propagates_engine_errors() -> None:
    Image = pytest.importorskip("PIL.Image")

    def boom(strip):
        raise RuntimeError("model exploded")

    engine = RapidOcrEngine(engine_factory=lambda: boom)
    buffer = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(buffer, format="PNG")
    with pytest.raises(RuntimeError, match="model exploded"):
        engine.extract_text(buffer.getvalue())


# --- the service loop ---------------------------------------------------------


class FakeEngine:
    def __init__(self, text: str = "raw text", *, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.loaded = False
        self.seen: list[bytes] = []

    def load(self) -> None:
        self.loaded = True

    def extract_text(self, data: bytes) -> str:
        self.seen.append(bytes(data))
        if self.fail:
            raise RuntimeError("boom")
        return self.text


def _service(db, engine, downloader, **overrides) -> OcrReaderService:
    settings = OcrReaderSettings(
        poll_seconds=1,
        heartbeat_seconds=30,
        job_lease_seconds=180,
        job_timeout_seconds=30,
        require_remote_database=False,
        **overrides,
    )
    return OcrReaderService(db, settings, engine=engine, downloader=downloader)


@pytest.mark.asyncio
async def test_process_next_job_empty_queue() -> None:
    async with _isolated_db() as db:
        service = _service(db, FakeEngine(), lambda url: asyncio.sleep(0, result=b""))
        assert await service.process_next_job() is False


@pytest.mark.asyncio
async def test_job_is_read_and_finished_with_its_url_cleared() -> None:
    async with _isolated_db() as db:
        engine = FakeEngine("Ninja AF101 Air Fryer, 4 Quart")
        service = _service(db, engine, lambda url: asyncio.sleep(0, result=b"pngbytes"))
        job_id = await submit_dm_ocr_job(db, USER_ID, URL)
        assert await service.process_next_job() is True
        assert engine.seen == [b"pngbytes"]
        stored = await get_dm_ocr_job(db, job_id)
        assert stored is not None
        assert stored.status == JOB_DONE
        assert stored.result_text == "Ninja AF101 Air Fryer, 4 Quart"
        assert stored.attachment_url is None


@pytest.mark.asyncio
async def test_download_failure_fails_the_job_and_the_loop_survives() -> None:
    async with _isolated_db() as db:
        async def bad_download(url: str) -> bytes:
            raise ConnectionError("cdn unreachable")

        service = _service(db, FakeEngine(), bad_download)
        first = await submit_dm_ocr_job(db, USER_ID, URL + "1")
        assert await service.process_next_job() is True
        stored = await get_dm_ocr_job(db, first)
        assert stored is not None
        assert stored.status == JOB_FAILED
        assert "ConnectionError" in (stored.error or "")
        # The loop is alive: a second job is claimed and also settled.
        second = await submit_dm_ocr_job(db, USER_ID, URL + "2")
        assert await service.process_next_job() is True
        stored2 = await get_dm_ocr_job(db, second)
        assert stored2 is not None and stored2.status == JOB_FAILED


@pytest.mark.asyncio
async def test_engine_failure_fails_the_job() -> None:
    async with _isolated_db() as db:
        service = _service(
            db,
            FakeEngine(fail=True),
            lambda url: asyncio.sleep(0, result=b"pngbytes"),
        )
        job_id = await submit_dm_ocr_job(db, USER_ID, URL)
        assert await service.process_next_job() is True
        stored = await get_dm_ocr_job(db, job_id)
        assert stored is not None
        assert stored.status == JOB_FAILED
        assert "RuntimeError" in (stored.error or "")


@pytest.mark.asyncio
async def test_oversize_download_fails_before_ocr() -> None:
    async with _isolated_db() as db:
        engine = FakeEngine()
        big = b"x" * (MAX_JOB_IMAGE_BYTES + 1)
        service = _service(db, engine, lambda url: asyncio.sleep(0, result=big))
        job_id = await submit_dm_ocr_job(db, USER_ID, URL)
        assert await service.process_next_job() is True
        stored = await get_dm_ocr_job(db, job_id)
        assert stored is not None and stored.status == JOB_FAILED
        assert "10 MB" in (stored.error or "")
        assert engine.seen == []  # never fed to the engine


@pytest.mark.asyncio
async def test_run_forever_touches_the_heartbeat_and_loads_the_engine() -> None:
    async with _isolated_db() as db:
        engine = FakeEngine()
        service = _service(db, engine, lambda url: asyncio.sleep(0, result=b""))
        task = asyncio.create_task(service.run_forever())
        try:
            await asyncio.sleep(0.3)
            assert engine.loaded is True
            assert await reader_heartbeat_fresh(db) is True
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
