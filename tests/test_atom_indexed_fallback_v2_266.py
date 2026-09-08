from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sniperplug.services.atom_indexed_promotions import (
    ATOM_INDEX_QUERY,
    BING_SEARCH_URL,
    AtomIndexedConfirmation,
    AtomIndexedPromotionsClient,
    normalize_atom_indexed_promotion_url,
    parse_bing_atom_confirmation_rss,
)
from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    AtomPromotionsClient,
    MovieTicketSourceState,
)
from sniperplug.services.movie_ticket_recovery import parse_current_atom_promotions_html


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        url: str,
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


def test_indexed_url_normalizer_accepts_only_hub_or_one_level_detail() -> None:
    assert normalize_atom_indexed_promotion_url(ATOM_PROMOTIONS_URL) == ATOM_PROMOTIONS_URL
    assert (
        normalize_atom_indexed_promotion_url(
            "https://atomtickets.com/promotions/tmobile-tuesdays-mutiny?utm_source=index#terms"
        )
        == f"{ATOM_PROMOTIONS_URL}/tmobile-tuesdays-mutiny"
    )

    rejected = (
        "http://www.atomtickets.com/promotions/tmobile-tuesdays-mutiny",
        "https://example.com/promotions/tmobile-tuesdays-mutiny",
        "https://www.atomtickets.com/help/promotions",
        "https://www.atomtickets.com/promotions/foo/bar",
        "https://www.atomtickets.com/promotions/%2Fadmin",
        "https://promotions.atomtickets.com/foo",
    )
    for value in rejected:
        assert normalize_atom_indexed_promotion_url(value) == ""


def test_bing_rss_accepts_official_detail_when_hub_is_absent() -> None:
    observed = "2026-09-08T12:00:00+00:00"
    rss = """
    <rss><channel>
      <item>
        <title>T-Mobile Tuesdays - Mutiny | Atom, Your Ticket to More</title>
        <link>https://www.atomtickets.com/promotions/tmobile-tuesdays-mutiny</link>
        <description>Offer valid for an Atom Tickets promotional ticket.</description>
      </item>
    </channel></rss>
    """
    confirmation = parse_bing_atom_confirmation_rss(rss, observed_at=observed)
    assert confirmation is not None
    assert confirmation.canonical_url == f"{ATOM_PROMOTIONS_URL}/tmobile-tuesdays-mutiny"
    assert confirmation.observed_at == observed

    parsed = parse_current_atom_promotions_html(confirmation.synthetic_html)
    assert parsed.document_valid is True
    assert parsed.drops == ()


def test_bing_rss_prefers_canonical_hub_over_detail_result() -> None:
    rss = """
    <rss><channel>
      <item>
        <title>T-Mobile Tuesdays - Mutiny | Atom, Your Ticket to More</title>
        <link>https://www.atomtickets.com/promotions/tmobile-tuesdays-mutiny</link>
        <description>Offer valid for an Atom promotional ticket.</description>
      </item>
      <item>
        <title>Atom Tickets Promotions | Promo Hub</title>
        <link>https://www.atomtickets.com/promotions</link>
        <description>Current Atom Tickets promotions and partner offers.</description>
      </item>
    </channel></rss>
    """
    confirmation = parse_bing_atom_confirmation_rss(rss)
    assert confirmation is not None
    assert confirmation.canonical_url == ATOM_PROMOTIONS_URL


def test_bing_rss_rejects_wrong_paths_hosts_and_non_atom_identity() -> None:
    rss = """
    <rss><channel>
      <item>
        <title>Atom promotion repost</title>
        <link>https://example.com/promotions/fake</link>
        <description>Atom ticket offer.</description>
      </item>
      <item>
        <title>Atom Tickets help</title>
        <link>https://www.atomtickets.com/help/promotions</link>
        <description>Promotion help.</description>
      </item>
      <item>
        <title>Atom Tickets nested result</title>
        <link>https://www.atomtickets.com/promotions/foo/bar</link>
        <description>Promotion offer.</description>
      </item>
      <item>
        <title>Unrelated page</title>
        <link>https://www.atomtickets.com/promotions/a-real-slug</link>
        <description>Nothing useful here.</description>
      </item>
    </channel></rss>
    """
    assert parse_bing_atom_confirmation_rss(rss) is None


@pytest.mark.asyncio
async def test_index_client_uses_directory_site_query_and_accepts_detail_page() -> None:
    rss = b"""
    <rss><channel><item>
      <title>Atom Rewards Refer a Friend Offer Terms</title>
      <link>https://www.atomtickets.com/promotions/atom-rewards-refer-a-friend-offer-terms</link>
      <description>Official Atom Tickets promotional offer terms.</description>
    </item></channel></rss>
    """
    session = FakeSession(
        [FakeResponse(200, url=BING_SEARCH_URL, body=rss)]
    )
    confirmation = await AtomIndexedPromotionsClient(session).confirm_canonical_page()

    assert confirmation.canonical_url.endswith("/promotions/atom-rewards-refer-a-friend-offer-terms")
    assert len(session.calls) == 1
    params = session.calls[0].get("params")
    assert isinstance(params, dict)
    query = str(params.get("q", ""))
    assert query == ATOM_INDEX_QUERY
    assert query.startswith("site:")
    assert "url:" not in query


@pytest.mark.asyncio
async def test_atom_403_can_recover_from_official_indexed_detail_without_codes(monkeypatch) -> None:
    observed = datetime.now(UTC).isoformat()
    confirmation = AtomIndexedConfirmation(
        canonical_url=f"{ATOM_PROMOTIONS_URL}/tmobile-tuesdays-mutiny",
        title="T-Mobile Tuesdays - Mutiny | Atom, Your Ticket to More",
        snippet="Official Atom Tickets offer terms.",
        observed_at=observed,
    )

    async def fake_confirm(self):
        return confirmation

    monkeypatch.setattr(AtomIndexedPromotionsClient, "confirm_canonical_page", fake_confirm)
    session = FakeSession(
        [FakeResponse(403, url=ATOM_PROMOTIONS_URL)]
    )
    result = await AtomPromotionsClient(session).fetch(MovieTicketSourceState())

    assert result.not_modified is False
    assert result.final_url == confirmation.canonical_url
    assert result.last_modified == confirmation.state_marker
    assert result.etag == ""
    parsed = parse_current_atom_promotions_html(result.html)
    assert parsed.document_valid is True
    assert parsed.drops == ()
    assert len(session.calls) == 1
