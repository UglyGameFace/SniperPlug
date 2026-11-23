"""Store finder: store-number extraction and the maps lookup's location.

Measured live 2026-11-23: SerpApi's google_maps engine returns zero
results without coordinates, and Home Depot store URLs carry the ZIP
before the store number (/l/<city>/<state>/<city>/<zip>/<store>), so
the finder must take the LAST numeric segment, not the first.
"""

from __future__ import annotations

import io
import json

from sniperplug.services import home_depot_store_finder as finder


BRIDGEPORT = {
    "title": "The Home Depot",
    "address": "656 Reservoir Ave, Bridgeport, CT 06606",
    "website": "https://www.homedepot.com/l/Bridgeport/CT/Bridgeport/06606/6213?emt=MSGoogleMaps",
}


def test_maps_choice_uses_the_store_number_not_the_zip():
    choice = finder._choice_from_maps_item(BRIDGEPORT)
    assert choice is not None
    assert choice.store_id == "6213"
    assert "Reservoir Ave" in choice.label


def test_html_choices_use_the_store_number_not_the_zip():
    body = '<a href="/l/Bridgeport/CT/Bridgeport/06606/6213">Bridgeport</a>'
    choices = list(finder._choices_from_html(body, max_results=8))
    assert [c.store_id for c in choices] == ["6213"]


def test_store_id_from_url_prefers_the_trailing_store_number():
    assert finder._store_id_from_url(BRIDGEPORT["website"]) == "6213"


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_maps_lookup_sends_geocoded_coordinates(monkeypatch):
    seen_urls: list[str] = []

    def fake_urlopen(url, timeout=None):
        seen_urls.append(str(url))
        if "zippopotam" in str(url):
            payload = {"places": [{"latitude": "41.2091", "longitude": "-73.2086"}]}
        else:
            payload = {"local_results": [BRIDGEPORT]}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    monkeypatch.setattr(finder.urllib.request, "urlopen", fake_urlopen)
    choices = finder._serpapi_maps_stores("06606", max_results=8)
    assert [c.store_id for c in choices] == ["6213"]
    serpapi_url = next(u for u in seen_urls if "serpapi" in u)
    assert "ll=%4041.2091%2C-73.2086%2C12z" in serpapi_url


def test_maps_lookup_still_works_when_geocoding_fails(monkeypatch):
    def fake_urlopen(url, timeout=None):
        if "zippopotam" in str(url):
            raise OSError("no route")
        return _FakeResponse(json.dumps({"local_results": [BRIDGEPORT]}).encode("utf-8"))

    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    monkeypatch.setattr(finder.urllib.request, "urlopen", fake_urlopen)
    choices = finder._serpapi_maps_stores("06606", max_results=8)
    assert [c.store_id for c in choices] == ["6213"]
