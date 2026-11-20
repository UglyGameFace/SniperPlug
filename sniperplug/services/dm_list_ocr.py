"""Picture reading for DM list intake: one small engine interface.

Felix's free-version rule (2026-11-19): read screenshots locally, no
paid image-recognition service. The bot's Discloud container is capped
at 256 MB and already idles at ~132-174 MB, so the engine choice was
measured before it was designed (2026-11-19, gate notes in the build
report ``sniperplug-dm-lists-build-20261119.md``):

* The live container has **no tesseract binary and no Pillow**
  (``which tesseract`` empty; ``import PIL`` raises), so the
  Pillow + pytesseract route cannot run there today.
* ``rapidocr-onnxruntime`` was evaluated locally on a synthetic
  Walmart-cart screenshot: engine load alone peaked at ~128 MB in a
  fresh process, and a tuned run (single-threaded, detection side
  capped, image sliced into strips) peaked at ~325 MB for the OCR
  process alone — over the whole container cap before the bot's own
  ~160 MB is counted. It is deliberately NOT wired in: installing it
  would OOM the production bot.

So the interface below is the feature, and the engines are honest
about availability:

* :class:`TesseractOcrEngine` — used only when a ``tesseract`` binary
  AND Pillow + pytesseract are actually present (none are, in the
  production container today). If a future host provides them, picture
  reading turns on with no other code change.
* :class:`UnavailableOcrEngine` — the honest fallback. Picture
  rows are saved as "couldn't read this one" and the intake reply says
  plainly that picture reading isn't on yet; links work in full.
* :class:`RemoteOcrEngine` — the production path since the reader
  shipped: a separate small Discloud app (``SniperPlug-Reader``,
  ``sniperplug.ocr_reader``) runs the measured rapidocr build in its
  own 512 MB container, and the bot hands it pictures through the
  shared database (``sniperplug.services.dm_ocr_jobs``). It cannot
  implement the synchronous interface — submitting a job and polling
  for its text is async work — so it exposes ``available()`` /
  ``read_text()`` coroutines and the intake's picture branch awaits
  them. Its availability is the reader's heartbeat, not an env flag:
  no fresh heartbeat, no remote reads, and intake falls back to the
  unavailable behavior on its own.

:func:`extract_candidate_lines` is pure text work — it turns raw OCR
output into plausible product-name search lines, dropping the prices,
quantities, totals and buttons that fill a cart screenshot — and is
fully unit-tested against canned OCR strings, so the day an engine
lands, only the engine is new.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil

log = logging.getLogger("sniperplug")

# At most this many product-name lines are taken from one picture; each
# line costs a search, and a cart screenshot rarely names more items.
MAX_CANDIDATE_LINES_PER_IMAGE = 8

# Lines that are pure screenshot furniture (buttons, totals, fulfilment
# notes), matched against the whole normalized line. A product name that
# merely contains one of these words ("Total War: Warhammer") is not
# furniture — only an exact whole-line match is dropped here; the other
# filters below handle the rest.
_NOISE_LINES = frozenset(
    {
        "cart",
        "your cart",
        "wishlist",
        "my wishlist",
        "subtotal",
        "total",
        "estimated total",
        "taxes",
        "checkout",
        "check out",
        "add to cart",
        "added to cart",
        "save for later",
        "saved for later",
        "remove",
        "see details",
        "details",
        "sponsored",
        "best seller",
        "pickup",
        "delivery",
        "free pickup",
        "free delivery",
        "pickup tomorrow",
        "pickup today",
        "delivery tomorrow",
        "in stock",
        "out of stock",
        "only a few left",
        "rollback",
        "clearance",
        "add to list",
        "add to registry",
        "buy now",
        "qty",
        "quantity",
    }
)

# Screenshot furniture that appears INSIDE longer lines (an Amazon
# cart's badges, buttons and delivery notes — measured on a real
# Amazon cart screenshot, 2026-11-19, where every extracted line was
# furniture and each one was searched as if it were a product).
# These phrases are store UI, never product names, so a substring
# match is safe here where the whole-line set above is not.
_NOISE_PHRASES = (
    "proceed to checkout",
    "search or ask a question",
    "best seller in",
    "bought in past month",
    "subscribe&save",
    "subscribe & save",
    "auto-delivery",
    "free returns",
    "free delivery",
    "prime tomorrow",
    "/ count",
    "applied at checkout",
    "conditions apply",
)

_PRICE_ONLY_RE = re.compile(r"^\$?\s*\d[\d,]*(\.\d{1,2})?(\s*(\$|usd|each|ea\.?))?$")
_QTY_RE = re.compile(r"^(qty|quantity)\s*:?\s*\d+$", re.IGNORECASE)
_COUNT_LINE_RE = re.compile(r"^(cart|wishlist)\s*\(\d+\s*items?\)$", re.IGNORECASE)
_TRAILING_PRICE_RE = re.compile(r"\s+\$?\d[\d,]*\.\d{2}\s*$")
_LEADING_BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\d{1,2}[.)])\s+")
_RATING_RE = re.compile(r"^\d(\.\d)?\s*(stars?|★)", re.IGNORECASE)


class OcrEngine:
    """The engine interface: image bytes in, raw text (or None) out.

    ``None`` means "no engine could read this" — either no engine is
    installed, or it failed on this image. Callers treat both the same:
    the picture becomes a "couldn't read this one" list row.
    """

    available: bool = False

    def extract_text(self, image_bytes: bytes) -> str | None:
        raise NotImplementedError


class UnavailableOcrEngine(OcrEngine):
    """No picture reading on this host. Honest, instant, dependency-free."""

    available = False

    def extract_text(self, image_bytes: bytes) -> str | None:
        return None


class TesseractOcrEngine(OcrEngine):
    """Pillow + pytesseract, active only where the binary really exists.

    Everything is imported lazily and re-checked per call: this module
    must import cleanly (and the bot must boot) on hosts with none of
    the pieces installed, which includes the production container.
    """

    def __init__(self) -> None:
        self._checked = False
        self._ok = False

    @property
    def available(self) -> bool:  # type: ignore[override]
        if not self._checked:
            self._checked = True
            self._ok = self._probe()
        return self._ok

    @staticmethod
    def _probe() -> bool:
        if shutil.which("tesseract") is None:
            return False
        try:
            import PIL.Image  # noqa: F401
            import pytesseract  # noqa: F401
        except Exception:  # noqa: BLE001 - absence is a normal state here.
            return False
        return True

    def extract_text(self, image_bytes: bytes) -> str | None:
        if not self.available:
            return None
        try:
            import io

            import pytesseract
            from PIL import Image

            image = Image.open(io.BytesIO(image_bytes)).convert("L")
            # Phone screenshots are tall and wide; cap the width so the
            # OCR pass stays bounded, keeping the aspect ratio.
            if image.width > 1200:
                image = image.resize(
                    (1200, int(image.height * 1200 / image.width))
                )
            return str(pytesseract.image_to_string(image) or "")
        except Exception:  # noqa: BLE001 - a bad image must not crash intake.
            log.exception("DM list OCR (tesseract) failed on one picture")
            return None


def get_ocr_engine() -> OcrEngine:
    """The best engine this host can actually run, checked honestly."""

    engine = TesseractOcrEngine()
    if engine.available:
        return engine
    return UnavailableOcrEngine()


# The reader checks in every ~30 s; three missed check-ins in a row
# means it is gone, not busy. A read is given two minutes end to end —
# the reader works one picture at a time and a phone screenshot takes
# seconds — after which the picture settles unread, like any failure.
READER_HEARTBEAT_MAX_AGE_SECONDS = 90.0
REMOTE_OCR_TIMEOUT_SECONDS = 120.0
REMOTE_OCR_POLL_SECONDS = 2.0


class RemoteOcrEngine:
    """Async client for the standalone SniperPlug-Reader app.

    The bot never downloads the picture for this path and never runs
    OCR itself: it queues a job carrying the attachment URL, and the
    reader (which polls the same table) downloads, reads, and writes
    back the raw text. ``read_text`` returns that text, or ``None``
    for every failure family — the job failed, the wait timed out,
    the heartbeat went stale mid-flight, or the job row vanished —
    because the intake settles all of them the same way: the picture
    becomes a "couldn't read this one" row, exactly like the
    no-engine path. A finished job's row is deleted once consumed,
    so result text and URLs do not pile up in the shared table.
    """

    def __init__(
        self,
        *,
        poll_seconds: float = REMOTE_OCR_POLL_SECONDS,
        timeout_seconds: float = REMOTE_OCR_TIMEOUT_SECONDS,
        heartbeat_max_age_seconds: float = READER_HEARTBEAT_MAX_AGE_SECONDS,
    ) -> None:
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.heartbeat_max_age_seconds = float(heartbeat_max_age_seconds)

    async def available(self, db: object) -> bool:
        """True when the reader's heartbeat is fresh. Never raises."""

        from sniperplug.services.dm_ocr_jobs import reader_heartbeat_fresh

        try:
            return await reader_heartbeat_fresh(
                db, max_age_seconds=self.heartbeat_max_age_seconds
            )
        except Exception:  # noqa: BLE001 - a DB hiccup means "not available".
            log.exception("DM list remote OCR heartbeat check failed")
            return False

    async def read_text(
        self, db: object, *, user_id: int, attachment_url: str
    ) -> str | None:
        """Queue one picture and wait for its raw OCR text (or None)."""

        from sniperplug.services.dm_ocr_jobs import (
            JOB_DONE,
            JOB_FAILED,
            get_dm_ocr_job,
            reader_heartbeat_fresh,
            submit_dm_ocr_job,
        )

        try:
            job_id = await submit_dm_ocr_job(db, user_id, attachment_url)
        except Exception:  # noqa: BLE001 - a failed submit is a failed read.
            log.exception("DM list remote OCR could not queue a picture")
            return None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        while True:
            try:
                job = await get_dm_ocr_job(db, job_id)
                if job is None:
                    return None
                if job.status == JOB_DONE:
                    text = job.result_text
                    await self._consume_quietly(db, job_id)
                    return text if text else None
                if job.status == JOB_FAILED:
                    await self._consume_quietly(db, job_id)
                    return None
                if loop.time() >= deadline:
                    return None
                if not await reader_heartbeat_fresh(
                    db, max_age_seconds=self.heartbeat_max_age_seconds
                ):
                    return None
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - settle unread, never hang intake.
                log.exception("DM list remote OCR poll failed")
                return None
            await asyncio.sleep(self.poll_seconds)

    @staticmethod
    async def _consume_quietly(db: object, job_id: int) -> None:
        from sniperplug.services.dm_ocr_jobs import consume_dm_ocr_job

        try:
            await consume_dm_ocr_job(db, job_id)
        except Exception:  # noqa: BLE001 - the reader's sweep is the backstop.
            log.exception("DM list remote OCR could not consume job %s", job_id)


def _line_is_noise(line: str) -> bool:
    lowered = " ".join(line.lower().split())
    if lowered in _NOISE_LINES:
        return True
    if any(phrase in lowered for phrase in _NOISE_PHRASES):
        return True
    if _COUNT_LINE_RE.match(lowered):
        return True
    if _QTY_RE.match(lowered):
        return True
    if _PRICE_ONLY_RE.match(lowered):
        return True
    if _RATING_RE.match(lowered):
        return True
    # A product name has letters; a line of digits/punctuation is a
    # price, a count, or a divider.
    if not any(ch.isalpha() for ch in line):
        return True
    return False


def extract_candidate_lines(
    text: str, *, limit: int = MAX_CANDIDATE_LINES_PER_IMAGE
) -> list[str]:
    """Plausible product-name lines from raw OCR text, in page order.

    Cart and wishlist screenshots interleave each product's name with
    its price, quantity and fulfilment notes. This keeps lines that
    read like product names and drops the furniture: price-only lines,
    ``Qty 1``, totals, buttons, ratings, and one-word fragments (OCR
    splits long names across lines; a one-word tail — "Unlocked",
    "White" — is too little to search with and is dropped). Longer
    wrap fragments are searched too, since plain text cannot prove
    they are continuations; storage dedupe by pinned item ID keeps
    one row per product when a fragment finds the same item. A
    trailing price on a name line is stripped. Duplicates (the same
    name read twice) collapse, order preserved.
    """

    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in str(text or "").splitlines():
        line = _LEADING_BULLET_RE.sub("", raw_line.strip())
        line = _TRAILING_PRICE_RE.sub("", line).strip(" -–—")
        line = " ".join(line.split())
        if len(line) < 10 or len(line.split()) < 2:
            continue
        if _line_is_noise(line):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
        if len(lines) >= max(1, int(limit)):
            break
    return lines
