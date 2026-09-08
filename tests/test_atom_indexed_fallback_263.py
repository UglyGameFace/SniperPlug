from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sniperplug.services.atom_indexed_promotions import (
    ATOM_INDEXED_FALLBACK_PREFIX,
    AtomIndexedConfirmation,
    AtomIndexedPromotionsClient,
    is_atom_indexed_fallback_marker,
    normalize_atom_canonical_url,
    parse_bing_atom_confirmation_rss,
)
from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    AtomPromotionsClient,
    MovieTicketSourceState,
)
from sniperplug.services.movie_ticket_recovery import (
    movie_source_health_text,
    parse_current_atom_promotions_html,
)


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        url: str = ATOM_PROMOTIONS_URL,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.url = url
        self._body = body
        self.headers = headers or {}
        self.charset = "utf-8"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self) -> bytes:
        return self._body


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("Unexpected HTTP request")
        return self.responses.pop(0)


def test_bing_rss_accepts_only_exact_canonical_atom_promotions_result() -> None:
    rss = """
    <rss><channel>
      <item>
        <title>Fake Atom promotion</title>
        <link>https://example.com/promotions</link>
        <description>Atom promotion repost</description>
      </item>
      <item>
        <title>Atom Tickets - Old Detail</title>
        <link>https://www.atomtickets.com/promotions/old-offer</link>
        <description>Old promotion detail</description>
      </item>
      <item>
        <title>Atom Tickets Promotions | Promo Hub</title>
        <link>https://www.atomtickets.com/promotions</link>
        <description>Welcome to the Atom Tickets promotions page and Partner Promotions.</description>
      </item>
    </channel></rss>
    """
    confirmation = parse_bing_atom_confirmation_rss(
        rss,
        observed_at="2026-09-08T00:00:00+00:00",
    )
    assert confirmation is not None
    assert confirmation.canonical_url == ATOM_PROMOTIONS_URL
    assert confirmation.state_marker == f"{ATOM_INDEXED_FALLBACK_PREFIX}2026-09-08T00:00:00+00:00"
    assert is_atom_indexed_fallback_marker(confirmation.state_marker)


def test_atom_canonical_normalizer_fails_closed_for_wrong_scheme_host_or_subpage() -> None:
    assert normalize_atom_canonical_url(ATOM_PROMOTIONS_URL) == ATOM_PROMOTIONS_URL
    assert normalize_atom_canonical_url(ATOM_PROMOTIONS_URL + "/") == ATOM_PROMOTIONS_URL
    assert normalize_atom_canonical_url("http://www.atomtickets.com/promotions") == ""
    assert normalize_atom_canonical_url("https://example.com/promotions") == ""
    assert normalize_atom_canonical_url("https://www.atomtickets.com/promotions/expired") == ""


def test_index_confirmation_shell_cannot_invent_a_public_reusable_code() -> None:
    confirmation = AtomIndexedConfirmation(
        canonical_url=ATOM_PROMOTIONS_URL,
        title="Atom Tickets Promotions",
        snippet="Partner Promotions: Samsung supplies its own customer code.",
        observed_at="2026-09-08T00:00:00+00:00",
    )
    parsed = parse_current_atom_promotions_html(confirmation.synthetic_html)
    assert parsed.document_valid is True
    assert parsed.partner_section_found is True
    assert parsed.film_section_found is False
    assert parsed.drops == ()


@pytest.mark.asyncio
async def test_atom_403_uses_index_confirmation_without_guessing_codes(monkeypatch) -> None:
    confirmation = AtomIndexedConfirmation(
        canonical_url=ATOM_PROMOTIONS_URL,
        title="Atom Tickets Promotions | Promo Hub",
        snippet="Partner Promotions are available on Atom.",
        observed_at="2026-09-08T00:00:00+00:00",
    )

    async def fake_confirm(self):
        return confirmation

    monkeypatch.setattr(AtomIndexedPromotionsClient, "confirm_canonical_page", fake_confirm)
    session = FakeSession([FakeResponse(403)])
    result = await AtomPromotionsClient(session).fetch(MovieTicketSourceState())

    assert result.not_modified is False
    assert result.final_url == ATOM_PROMOTIONS_URL
    assert result.etag == ""
    assert result.last_modified == confirmation.state_marker
    parsed = parse_current_atom_promotions_html(result.html)
    assert parsed.document_valid is True
    assert parsed.drops == ()
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_non_access_atom_http_failure_does_not_use_index_fallback(monkeypatch) -> None:
    async def should_not_run(self):
        raise AssertionError("Indexed fallback must not run for HTTP 500")

    monkeypatch.setattr(AtomIndexedPromotionsClient, "confirm_canonical_page", should_not_run)
    session = FakeSession([FakeResponse(500)])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await AtomPromotionsClient(session).fetch(MovieTicketSourceState())
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_index_marker_is_never_sent_to_atom_as_http_validator() -> None:
    direct_html = b"""
    <html><head><title>Atom Tickets Promotions</title></head><body>
      <h2>Partner Promotions</h2>
    </body></html>
    """
    session = FakeSession(
        [
            FakeResponse(
                200,
                body=direct_html,
                headers={"ETag": '"new-etag"', "Last-Modified": "Tue, 08 Sep 2026 00:00:00 GMT"},
            )
        ]
    )
    state = MovieTicketSourceState(
        etag='"stale-etag"',
        last_modified=f"{ATOM_INDEXED_FALLBACK_PREFIX}2026-09-07T23:00:00+00:00",
    )
    result = await AtomPromotionsClient(session).fetch(state)

    assert result.not_modified is False
    assert result.etag == '"new-etag"'
    assert result.last_modified == "Tue, 08 Sep 2026 00:00:00 GMT"
    headers = session.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert "If-None-Match" not in headers
    assert "If-Modified-Since" not in headers


def test_atom_health_distinguishes_indexed_confirmation_from_direct_health() -> None:
    now = datetime(2026, 9, 8, 0, 30, tzinfo=UTC)
    state = MovieTicketSourceState(
        source_key="atom_official_promotions",
        last_success_at="2026-09-08T00:20:00+00:00",
        last_modified=f"{ATOM_INDEXED_FALLBACK_PREFIX}2026-09-08T00:20:00+00:00",
        active_drop_count=0,
    )
    line = movie_source_health_text(
        "Atom",
        state,
        official_url=ATOM_PROMOTIONS_URL,
        now=now,
    )
    assert "Indexed fallback current" in line
    assert "direct Atom HTTP access blocked" in line
    assert "public reusable codes are not inferred" in line
    assert "Healthy" not in line
