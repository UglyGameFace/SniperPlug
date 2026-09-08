from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sniperplug.services.atom_indexed_promotions import (
    ATOM_DIRECT_REPROBE_SECONDS,
    ATOM_INDEX_RECHECK_SECONDS,
    ATOM_INDEXED_FALLBACK_PREFIX,
    AtomIndexedConfirmation,
    AtomIndexedPromotionsClient,
    build_atom_indexed_fallback_marker,
    is_atom_indexed_fallback_marker,
    normalize_atom_canonical_url,
    parse_atom_indexed_fallback_marker,
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


def _ago(seconds: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


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
    observed = "2026-09-08T00:00:00+00:00"
    confirmation = parse_bing_atom_confirmation_rss(rss, observed_at=observed)
    assert confirmation is not None
    assert confirmation.canonical_url == ATOM_PROMOTIONS_URL
    assert is_atom_indexed_fallback_marker(confirmation.state_marker)
    marker = parse_atom_indexed_fallback_marker(confirmation.state_marker)
    assert marker is not None
    assert marker.indexed_at == observed
    assert marker.direct_blocked_at == observed


def test_atom_marker_remains_backward_compatible_with_initial_263_shape() -> None:
    old_marker = f"{ATOM_INDEXED_FALLBACK_PREFIX}2026-09-08T00:00:00+00:00"
    parsed = parse_atom_indexed_fallback_marker(old_marker)
    assert parsed is not None
    assert parsed.indexed_at == "2026-09-08T00:00:00+00:00"
    assert parsed.direct_blocked_at == parsed.indexed_at


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
        observed_at=datetime.now(UTC).isoformat(),
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
async def test_fresh_index_confirmation_skips_atom_and_bing_requests(monkeypatch) -> None:
    async def should_not_run(self):
        raise AssertionError("Bing must not refresh inside the 30-minute index window")

    monkeypatch.setattr(AtomIndexedPromotionsClient, "confirm_canonical_page", should_not_run)
    marker = build_atom_indexed_fallback_marker(
        indexed_at=_ago(5 * 60),
        direct_blocked_at=_ago(10 * 60),
    )
    session = FakeSession([])
    result = await AtomPromotionsClient(session).fetch(
        MovieTicketSourceState(last_modified=marker)
    )

    assert result.not_modified is True
    assert result.last_modified == marker
    assert session.calls == []


@pytest.mark.asyncio
async def test_index_refresh_does_not_reprobe_atom_or_reset_direct_block_time(monkeypatch) -> None:
    original_direct_blocked = _ago(60 * 60)
    marker = build_atom_indexed_fallback_marker(
        indexed_at=_ago(ATOM_INDEX_RECHECK_SECONDS + 60),
        direct_blocked_at=original_direct_blocked,
    )
    confirmation = AtomIndexedConfirmation(
        canonical_url=ATOM_PROMOTIONS_URL,
        title="Atom Tickets Promotions",
        snippet="Canonical Atom promotions result.",
        observed_at=datetime.now(UTC).isoformat(),
    )

    async def fake_confirm(self):
        return confirmation

    monkeypatch.setattr(AtomIndexedPromotionsClient, "confirm_canonical_page", fake_confirm)
    session = FakeSession([])
    result = await AtomPromotionsClient(session).fetch(
        MovieTicketSourceState(last_modified=marker)
    )

    assert result.not_modified is False
    assert session.calls == []
    refreshed = parse_atom_indexed_fallback_marker(result.last_modified)
    assert refreshed is not None
    assert refreshed.indexed_at == confirmation.observed_at
    assert refreshed.direct_blocked_at == original_direct_blocked


@pytest.mark.asyncio
async def test_six_hour_cooldown_reprobes_atom_and_restores_direct_state(monkeypatch) -> None:
    async def should_not_run(self):
        raise AssertionError("Bing must not run after a successful direct Atom reprobe")

    monkeypatch.setattr(AtomIndexedPromotionsClient, "confirm_canonical_page", should_not_run)
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
    marker = build_atom_indexed_fallback_marker(
        indexed_at=_ago(5 * 60),
        direct_blocked_at=_ago(ATOM_DIRECT_REPROBE_SECONDS + 60),
    )
    state = MovieTicketSourceState(
        etag='"stale-etag"',
        last_modified=marker,
    )
    result = await AtomPromotionsClient(session).fetch(state)

    assert result.not_modified is False
    assert result.etag == '"new-etag"'
    assert result.last_modified == "Tue, 08 Sep 2026 00:00:00 GMT"
    assert len(session.calls) == 1
    headers = session.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert "If-None-Match" not in headers
    assert "If-Modified-Since" not in headers


@pytest.mark.asyncio
async def test_non_access_atom_http_failure_does_not_use_index_fallback(monkeypatch) -> None:
    async def should_not_run(self):
        raise AssertionError("Indexed fallback must not run for HTTP 500")

    monkeypatch.setattr(AtomIndexedPromotionsClient, "confirm_canonical_page", should_not_run)
    session = FakeSession([FakeResponse(500)])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await AtomPromotionsClient(session).fetch(MovieTicketSourceState())
    assert len(session.calls) == 1


def test_atom_health_uses_real_index_confirmation_time_not_cache_reuse_time() -> None:
    now = datetime(2026, 9, 8, 1, 0, tzinfo=UTC)
    indexed_at = datetime(2026, 9, 8, 0, 30, tzinfo=UTC)
    direct_blocked_at = datetime(2026, 9, 8, 0, 20, tzinfo=UTC)
    marker = build_atom_indexed_fallback_marker(
        indexed_at=indexed_at.isoformat(),
        direct_blocked_at=direct_blocked_at.isoformat(),
    )
    state = MovieTicketSourceState(
        source_key="atom_official_promotions",
        # The scan loop may reuse the fallback cache moments ago. Health must not
        # claim Bing was also reconfirmed moments ago.
        last_success_at="2026-09-08T00:59:50+00:00",
        last_modified=marker,
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
    assert f"<t:{int(indexed_at.timestamp())}:R>" in line
    assert f"<t:{int(direct_blocked_at.timestamp())}:R>" in line
    assert f"<t:{int(datetime(2026, 9, 8, 0, 59, 50, tzinfo=UTC).timestamp())}:R>" not in line
    assert "public reusable codes are not inferred" in line
    assert "Healthy" not in line
