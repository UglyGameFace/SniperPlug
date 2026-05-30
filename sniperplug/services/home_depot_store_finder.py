from __future__ import annotations

import asyncio
import html
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable


STORE_SEARCH_URL = "https://www.homedepot.com/l/search/"
STORE_LINK_RE = re.compile(r"/l/([^\"'<>]+?)/(\d{3,6})(?:[?\"'<>/]|$)")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class HomeDepotStoreChoice:
    store_id: str
    label: str
    url: str
    raw_path: str = ""

    @property
    def short_label(self) -> str:
        text = self.label.strip() or f"Home Depot #{self.store_id}"
        if self.store_id not in text:
            text = f"{text} #{self.store_id}"
        return text[:100]


async def find_home_depot_stores(zip_code: str, *, max_results: int = 8) -> tuple[HomeDepotStoreChoice, ...]:
    return await asyncio.to_thread(_find_home_depot_stores_sync, zip_code, max_results=max_results)


def _find_home_depot_stores_sync(zip_code: str, *, max_results: int = 8) -> tuple[HomeDepotStoreChoice, ...]:
    cleaned_zip = str(zip_code).strip()
    if not cleaned_zip:
        return ()
    url = STORE_SEARCH_URL + urllib.parse.quote(cleaned_zip)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 SniperPlug local stock checker",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception:
        return ()
    return tuple(_choices_from_html(body, max_results=max_results))


def _choices_from_html(body: str, *, max_results: int) -> Iterable[HomeDepotStoreChoice]:
    seen: set[str] = set()
    count = 0
    for raw_path, store_id in STORE_LINK_RE.findall(body):
        if store_id in seen:
            continue
        seen.add(store_id)
        label = _label_from_path(raw_path, store_id)
        url = f"https://www.homedepot.com/l/{raw_path}/{store_id}"
        yield HomeDepotStoreChoice(store_id=store_id, label=label, url=url, raw_path=raw_path)
        count += 1
        if count >= max_results:
            return


def _label_from_path(raw_path: str, store_id: str) -> str:
    parts = [urllib.parse.unquote(part) for part in raw_path.split("/") if part]
    if not parts:
        return f"Home Depot #{store_id}"
    cleaned = [html.unescape(SPACE_RE.sub(" ", part.replace("-", " ")).strip()) for part in parts]
    # Typical path shape: Store-Name/ST/City/Zip
    store_name = cleaned[0]
    state = cleaned[1] if len(cleaned) > 1 else ""
    city = cleaned[2] if len(cleaned) > 2 else ""
    zip_code = cleaned[3] if len(cleaned) > 3 else ""
    location = ", ".join(part for part in (city, state) if part)
    suffix = f" {zip_code}" if zip_code else ""
    if location:
        return f"{store_name} — {location}{suffix} #{store_id}"
    return f"{store_name} #{store_id}"
