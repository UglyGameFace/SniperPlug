from __future__ import annotations

import asyncio
from typing import Any

from sniperplug.cogs.movie_command_guide import (
    GUIDE_ENABLE_HERE_ID,
    MovieStatusActionView,
    build_movie_status_embed,
)
from sniperplug.services.gofobo_screenings import (
    GOFOBO_HOME_URL,
    GOFOBO_LOCAL_SCREENINGS_URL,
    GOFOBO_SOURCE_KEY,
    GofoboUpcomingClient,
    clean_gofobo_candidate_title,
    gofobo_offer_is_title_specific,
    parse_gofobo_home_html,
    safe_gofobo_image_url,
)
from sniperplug.services.movie_ticket_drops import MovieTicketSourceState


CURRENT_GOFOBO_CARD_SHAPE = """
<!doctype html>
<html>
<head><title>Gofobo | Movie Screenings</title></head>
<body>
  <a href="/main/redeem">REDEEM</a>
  <a href="/main/local_screenings/">Find Screenings</a>

  <h1>UPCOMING SCREENINGS &amp; EVENTS</h1>
  <article>
    <img src="https://d21ld3wfckjpu2.cloudfront.net/practical-magic-2.jpg">
    <h4>PRACTICAL MAGIC 2</h4>
    <a href="/main/local_movie_screenings/7001">LEARN MORE</a>
  </article>
  <article>
    <h5>HEART OF THE BEAST</h5>
    <a href="/main/local_screenings/">MORE INFO</a>
  </article>
  <article>
    <a href="/main/local_movie_screenings/7003">FORGOTTEN ISLAND LEARN MORE</a>
  </article>

  <h1>BROWSE OUR NEWLY ADDED MOVIES AND EVENTS</h1>
  <h2>ENTER OUR FEATURED SWEEPSTAKES</h2>
</body>
</html>
"""


class _FakeResponse:
    def __init__(self, status: int, *, payload: bytes = b"", headers: dict[str, str] | None = None):
        self.status = status
        self._payload = payload
        self.headers = headers or {}
        self.url = GOFOBO_HOME_URL
        self.charset = "utf-8"

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def read(self) -> bytes:
        return self._payload


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = list(responses)
        self.request_headers: list[dict[str, str]] = []

    def get(self, _url: str, *, headers: dict[str, str], **_: Any) -> _FakeResponse:
        self.request_headers.append(dict(headers))
        return self.responses.pop(0)


def test_current_gofobo_cta_shape_keeps_real_titles_and_drops_cta_labels() -> None:
    result = parse_gofobo_home_html(CURRENT_GOFOBO_CARD_SHAPE)

    assert result.document_valid is True
    titles = {drop.title for drop in result.drops}
    assert titles == {"PRACTICAL MAGIC 2", "HEART OF THE BEAST", "FORGOTTEN ISLAND"}
    assert "LEARN MORE" not in titles
    assert "MORE INFO" not in titles


def test_gofobo_prefers_title_specific_detail_link_but_has_safe_zip_fallback() -> None:
    drops = {drop.title: drop for drop in parse_gofobo_home_html(CURRENT_GOFOBO_CARD_SHAPE).drops}

    assert drops["PRACTICAL MAGIC 2"].offer_url.endswith("/main/local_movie_screenings/7001")
    assert gofobo_offer_is_title_specific(drops["PRACTICAL MAGIC 2"].offer_url) is True
    assert drops["HEART OF THE BEAST"].offer_url == GOFOBO_LOCAL_SCREENINGS_URL
    assert gofobo_offer_is_title_specific(drops["HEART OF THE BEAST"].offer_url) is False
    assert "actual theater, date, time" in drops["HEART OF THE BEAST"].validity_text


def test_gofobo_cta_cleanup_and_current_official_image_host() -> None:
    assert clean_gofobo_candidate_title("LEARN MORE") == ""
    assert clean_gofobo_candidate_title("FORGOTTEN ISLAND LEARN MORE") == "FORGOTTEN ISLAND"
    assert clean_gofobo_candidate_title("PRACTICAL MAGIC 2 | MORE INFO") == "PRACTICAL MAGIC 2"
    assert safe_gofobo_image_url("https://d21ld3wfckjpu2.cloudfront.net/poster.jpg")


def test_first_gofobo_fetch_after_process_start_ignores_old_validators_once() -> None:
    state = MovieTicketSourceState(
        source_key=GOFOBO_SOURCE_KEY,
        etag='"old-etag"',
        last_modified="Sun, 06 Sep 2026 12:00:00 GMT",
    )
    session = _FakeSession(
        [
            _FakeResponse(200, payload=b"<html>fresh Gofobo body</html>", headers={"ETag": '"new-etag"'}),
            _FakeResponse(304, headers={"ETag": '"new-etag"'}),
        ]
    )
    original = GofoboUpcomingClient._process_full_body_confirmed
    try:
        GofoboUpcomingClient._process_full_body_confirmed = False
        first = asyncio.run(GofoboUpcomingClient(session).fetch(state))  # type: ignore[arg-type]
        second = asyncio.run(GofoboUpcomingClient(session).fetch(state))  # type: ignore[arg-type]
    finally:
        GofoboUpcomingClient._process_full_body_confirmed = original

    assert first.not_modified is False
    assert "If-None-Match" not in session.request_headers[0]
    assert "If-Modified-Since" not in session.request_headers[0]
    assert second.not_modified is True
    assert session.request_headers[1]["If-None-Match"] == '"old-etag"'
    assert session.request_headers[1]["If-Modified-Since"] == "Sun, 06 Sep 2026 12:00:00 GMT"


def test_status_panel_exposes_one_click_current_channel_setup() -> None:
    view = MovieStatusActionView(None)  # type: ignore[arg-type]
    button = next(item for item in view.children if getattr(item, "custom_id", None) == GUIDE_ENABLE_HERE_ID)

    assert button.label == "Use This Channel for Alerts"
    embed = build_movie_status_embed(
        enabled=False,
        alert_channel_id=None,
        active_count=0,
        local_screening_count=3,
        sent_count=0,
        last_success_at="",
        source_health_lines=["Gofobo: Healthy"],
    )
    rendered = "\n".join(f"{field.name}\n{field.value}" for field in embed.fields)
    assert "Use This Channel for Alerts" in rendered
    assert "No slash-command syntax required" in rendered
