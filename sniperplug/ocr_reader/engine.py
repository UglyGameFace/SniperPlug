"""The reader's OCR engine: rapidocr-onnxruntime, tuned to fit.

Every setting here is the measured one from the 2026-11-19 OCR gate
evaluation (artifacts in ``~/workspace/ocr-eval-20261119``), which is
the whole reason this app exists at 512 MB:

* the image is converted to grayscale and resized to at most
  ``MAX_IMAGE_WIDTH`` px wide, keeping the aspect ratio exactly —
  phone screenshots are far larger than their text needs;
* onnxruntime runs single-threaded and the detection side is capped
  at ``DET_LIMIT_SIDE_LEN`` px;
* the page is read in horizontal strips (``STRIP_HEIGHT`` px with a
  ``STRIP_OVERLAP`` px overlap so a text line split by a strip edge
  is still read whole in one of them), with buffers dropped and
  ``gc.collect()`` between strips.

That build peaked at ~325 MB for the OCR process on the evaluation
screenshot and read all 5 product names. The engine loads once and
stays resident (~128 MB); a lock serializes reads so a slow strip
can never stack two OCR passes on top of each other.

Pillow and rapidocr are imported lazily: this module must import
cleanly on hosts without them (the main bot's test suite imports the
package for its wiring, and the bot itself never runs this engine).
"""

from __future__ import annotations

import gc
import io
import logging
import threading

log = logging.getLogger("sniperplug.ocr_reader")

MAX_IMAGE_WIDTH = 1000
STRIP_HEIGHT = 900
STRIP_OVERLAP = 60
DET_LIMIT_SIDE_LEN = 736


def strip_boxes(
    height: int, *, strip_height: int = STRIP_HEIGHT, overlap: int = STRIP_OVERLAP
) -> list[tuple[int, int]]:
    """(top, bottom) rows of each strip, covering ``height`` exactly."""

    if height <= 0:
        return []
    step = max(1, int(strip_height) - int(overlap))
    boxes: list[tuple[int, int]] = []
    top = 0
    while top < height:
        bottom = min(height, top + int(strip_height))
        boxes.append((top, bottom))
        if bottom >= height:
            break
        top += step
    return boxes


def preprocess_image(image: object) -> object:
    """Grayscale + downscale to ``MAX_IMAGE_WIDTH``, aspect kept.

    The proportions are never changed: the height scales by the same
    factor as the width, so nothing is stretched or squashed.
    """

    img = image.convert("L")  # type: ignore[attr-defined]
    if img.width > MAX_IMAGE_WIDTH:
        img = img.resize((MAX_IMAGE_WIDTH, int(img.height * MAX_IMAGE_WIDTH / img.width)))
    return img


def _import_rapidocr() -> object:
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR


def _repair_opencv_headless() -> None:
    """Install the headless OpenCV build over the bundled one.

    Discloud's Python image ships no libGL, and the opencv-python
    wheel rapidocr depends on links against it, so ``import cv2``
    dies with "libGL.so.1: cannot open shared object file" — proven
    live in this app's own container on 2026-11-19, where swapping in
    opencv-python-headless (the same library without the GUI parts)
    made the import work in the same container. Discloud's BUILD step
    runs a single command with no shell chaining, so the swap cannot
    be sequenced at build time; it happens here, once, the first time
    the import fails. If the in-process retry still fails, the
    service exits and the app's auto-restart gets a fresh process in
    which the headless build is already installed.
    """

    import subprocess
    import sys

    log.warning(
        "rapidocr import failed (the bundled OpenCV needs libGL, which "
        "this host does not have) — installing opencv-python-headless "
        "and retrying"
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            "opencv-python-headless",
        ],
        check=True,
        timeout=600,
    )


def _load_rapidocr_class(import_fn=None, repair_fn=None) -> object:
    """Import RapidOCR, repairing the OpenCV build once if needed."""

    import importlib

    importer = import_fn or _import_rapidocr
    try:
        return importer()
    except ImportError:
        (repair_fn or _repair_opencv_headless)()
        importlib.invalidate_caches()
        return importer()


def _default_engine_factory() -> object:
    rapidocr = _load_rapidocr_class()
    return rapidocr(  # type: ignore[operator]
        intra_op_num_threads=1,
        inter_op_num_threads=1,
        det_limit_side_len=DET_LIMIT_SIDE_LEN,
    )


class RapidOcrEngine:
    """One resident rapidocr engine behind a lock.

    ``extract_text`` returns the raw text (possibly empty when the
    picture holds no readable text) and raises on engine failure —
    the reader service turns the raise into a failed job and an
    empty string into a finished job with no lines, which are
    different outcomes for the person who sent the picture.
    """

    def __init__(self, *, engine_factory: object = None) -> None:
        self._factory = engine_factory or _default_engine_factory
        self._engine: object = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._engine is not None

    def load(self) -> None:
        if self._engine is None:
            self._engine = self._factory()  # type: ignore[operator]
            log.info("OCR engine loaded")

    def extract_text(self, image_bytes: bytes) -> str:
        with self._lock:
            return self._extract_locked(image_bytes)

    def _extract_locked(self, image_bytes: bytes) -> str:
        self.load()
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        image = preprocess_image(image)
        texts: list[str] = []
        for top, bottom in strip_boxes(int(image.height)):
            strip = image.crop((0, top, image.width, bottom))
            result, _elapsed = self._engine(strip)  # type: ignore[operator]
            for row in result or []:
                text = str(row[1] or "").strip()
                if text:
                    texts.append(text)
            del strip, result
            gc.collect()
        del image
        gc.collect()
        return "\n".join(texts)
