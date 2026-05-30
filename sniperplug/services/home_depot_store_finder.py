from __future__ import annotations

import asyncio
import html
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable


STORE_SEARCH_URL = "https://www.homedepot.com/l/search/"
SERPAPI_URL = "https://serpapi.com/search.json"
STORE_LINK_RE = re.compile(r"/l/([^\"'<>]+?)/(\d{3,6})(?:[?\"'<>/]|$)")
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
    direct = _home_depot_site_stores(cleaned_zip, max_results=max_results)
    if direct:
        return direct
    maps = _serpapi_maps_stores(cleaned_zip, max_results=max_results)
    if maps:
        return maps
    return ()


def _home_depot_site_stores(zip_code: str, *, max_results: int) -> tuple[HomeDepotStoreChoice, ...]:
    request = urllib.request.Request(
        STORE_SEARCH_URL + urllib.parse.quote(zip_code),
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


def _serpapi_maps_stores(zip_code: str, *, max_results: int) -> tuple[HomeDepotStoreChoice, ...]:
    key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not key:
        return ()
    params = {
        "engine": "google_maps",
        "q": f"Home Depot near {zip_code}",
        "type": "search",
        "api_key": key,
        "hl": "en",
    }
    try:
        with urllib.request.urlopen(f"{SERPAPI_URL}?{urllib.parse.urlencode(params)}", timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return ()
    if not isinstance(payload, dict):
        return ()
    raw_results = payload.get("local_results") or payload.get("places_results") or []
    if not isinstance(raw_results, list):
        return ()
    choices: list[HomeDepotStoreChoice] = []
    seen: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        choice = _choice_from_maps_item(item)
        if not choice or choice.store_id in seen:
            continue
        seen.add(choice.store_id)
        choices.append(choice)
        if len(choices) >= max_results:
            break
    return tuple(choices)


def _choice_from_maps_item(item: dict[str, Any]) -> HomeDepotStoreChoice | None:
    title = _clean(item.get("title")) or "Home Depot"
    address = _clean(item.get("address")) or _clean(item.get("subtitle")) or ""
    website = _clean(item.get("website")) or _clean(item.get("link")) or _clean(item.get("place_id_search")) or ""
    store_id = _store_id_from_url(website)
    if not store_id:
        # Some map results only expose a Home Depot URL inside the nested links.
        for value in item.values():
            if isinstance(value, str):
                store_id = _store_id_from_url(value)
                if store_id:
                    website = value
                    break
    if not store_id:
        return None
    label = f"{title} — {address}" if address else title
    url = website if website.startswith("http") else f"https://www.homedepot.com/l/search/{store_id}"
    return HomeDepotStoreChoice(store_id=store_id, label=label, url=url)


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


def _store_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = STORE_LINK_RE.search(url)
    if match:
        return match.group(2)
    match = re.search(r"(?:store|store_id|storeid|storeNumber|store_number)[=/:-]?(\d{3,6})", url, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _label_from_path(raw_path: str, store_id: str) -> str:
    parts = [urllib.parse.unquote(part) for part in raw_path.split("/") if part]
    if not parts:
        return f"Home Depot #{store_id}"
    cleaned = [html.unescape(SPACE_RE.sub(" ", part.replace("-", " ")).strip()) for part in parts]
    store_name = cleaned[0]
    state = cleaned[1] if len(cleaned) > 1 else ""
    city = cleaned[2] if len(cleaned) > 2 else ""
    zip_code = cleaned[3] if len(cleaned) > 3 else ""
    location = ", ".join(part for part in (city, state) if part)
    suffix = f" {zip_code}" if zip_code else ""
    if location:
        return f"{store_name} — {location}{suffix} #{store_id}"
    return f"{store_name} #{store_id}"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
