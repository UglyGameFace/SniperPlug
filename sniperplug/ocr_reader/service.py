"""The reader loop: claim one picture, read it, write the text back.

One job at a time, on purpose — the OCR engine's memory peak is the
reason this app exists, and two reads at once would double it. The
loop never dies on a job: a download failure, a corrupt picture, or
an engine error marks that one job failed and the loop moves on.
After every job the image buffers are dropped, garbage is collected,
and the process's memory use is logged, so the 512 MB budget stays
observable in the app's own logs.

The heartbeat is a separate task: it refreshes the reader's row in
``dm_ocr_reader_heartbeat`` (that freshness is what tells the bot
picture reading is up) and sweeps job rows older than an hour (the
backstop that bounds how long any attachment URL can sit in the
table).
"""

from __future__ import annotations

import asyncio
import gc
import logging
import resource
from typing import Any, Awaitable, Callable

from sniperplug.ocr_reader.config import OcrReaderSettings
from sniperplug.ocr_reader.engine import RapidOcrEngine
from sniperplug.services.dm_ocr_jobs import (
    STALE_JOB_MAX_AGE_SECONDS,
    claim_next_dm_ocr_job,
    delete_stale_dm_ocr_jobs,
    ensure_dm_ocr_tables,
    fail_dm_ocr_job,
    finish_dm_ocr_job,
    touch_reader_heartbeat,
)

log = logging.getLogger("sniperplug.ocr_reader")

MAX_JOB_IMAGE_BYTES = 10 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 256 * 1024


def current_rss_mb() -> float:
    """This process's current memory use, in MB."""

    try:
        with open("/proc/self/status") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


class OcrReaderService:
    def __init__(
        self,
        db: Any,
        settings: OcrReaderSettings,
        *,
        engine: RapidOcrEngine | None = None,
        downloader: Callable[[str], Awaitable[bytes]] | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.engine = engine if engine is not None else RapidOcrEngine()
        self._downloader = downloader

    # -- the loop ------------------------------------------------------

    async def run_forever(self) -> None:
        await ensure_dm_ocr_tables(self.db)
        # Load the engine up front: boot is when a missing or broken
        # engine should say so, not the first time someone sends a
        # picture. A load failure is fatal to the app (Discloud
        # restarts it), never silently absorbed.
        await asyncio.to_thread(self.engine.load)
        await touch_reader_heartbeat(self.db, rss_mb=current_rss_mb())
        heartbeat = asyncio.create_task(self._heartbeat_loop())
        log.info(
            "picture reader ready rss_mb=%.1f poll_s=%s lease_s=%s",
            current_rss_mb(),
            self.settings.poll_seconds,
            self.settings.job_lease_seconds,
        )
        try:
            while True:
                try:
                    worked = await self.process_next_job()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - the loop must survive.
                    log.exception("picture reader loop hit an unexpected error")
                    worked = False
                if not worked:
                    await asyncio.sleep(self.settings.poll_seconds)
        finally:
            heartbeat.cancel()

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_seconds)
            try:
                await touch_reader_heartbeat(self.db, rss_mb=current_rss_mb())
                swept = await delete_stale_dm_ocr_jobs(
                    self.db, max_age_seconds=STALE_JOB_MAX_AGE_SECONDS
                )
                if swept:
                    log.info("picture reader swept %s stale job row(s)", swept)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a missed tick is not fatal.
                log.exception("picture reader heartbeat failed")

    # -- one job ---------------------------------------------------------

    async def process_next_job(self) -> bool:
        """Claim and settle one job. False when the queue is empty."""

        job = await claim_next_dm_ocr_job(
            self.db, lease_seconds=self.settings.job_lease_seconds
        )
        if job is None:
            return False
        data: bytes | None = None
        try:
            data = await self._download(job.attachment_url or "")
            if len(data) > MAX_JOB_IMAGE_BYTES:
                raise ValueError("picture is larger than the 10 MB reader limit")
            text = await asyncio.wait_for(
                asyncio.to_thread(self.engine.extract_text, data),
                timeout=self.settings.job_timeout_seconds,
            )
            await finish_dm_ocr_job(self.db, job.id, result_text=text or "")
            log.info(
                "picture job %s read: %s chars rss_mb=%.1f peak_mb=%.1f",
                job.id,
                len(text or ""),
                current_rss_mb(),
                peak_rss_mb(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad picture, one failed job.
            log.exception("picture job %s failed", job.id)
            try:
                await fail_dm_ocr_job(
                    self.db, job.id, error=f"{type(exc).__name__}: {exc}"
                )
            except Exception:  # noqa: BLE001 - the lease will reclaim it.
                log.exception("picture job %s could not be marked failed", job.id)
        finally:
            data = None
            gc.collect()
        return True

    async def _download(self, url: str) -> bytes:
        if self._downloader is not None:
            return await self._downloader(url)
        return await self._download_http(url)

    async def _download_http(self, url: str) -> bytes:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self.settings.job_timeout_seconds)
        headers = {"User-Agent": self.settings.user_agent}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_BYTES):
                    total += len(chunk)
                    if total > MAX_JOB_IMAGE_BYTES:
                        raise ValueError(
                            "picture is larger than the 10 MB reader limit"
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
