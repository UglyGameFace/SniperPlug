from __future__ import annotations

from urllib.parse import quote

import pytest

from sniperplug.services.atom_indexed_promotions import (
    ATOM_INDEX_QUERY,
    BING_SEARCH_URL,
    DUCKDUCKGO_INDEX_QUERY,
    DUCKDUCKGO_SEARCH_URL,
    AtomIndexedPromotionsClient,
    _decode_duckduckgo_result_url,
    parse_duckduckgo_atom_confirmation_html,
)
from sniperplug.services.movie_ticket_drops import ATOM_PROMOTIONS_URL
from sniperplug.services.movie_ticket_recovery import parse_current_atom_promotions_html


class FakeResponse:
    def __init__(self, status: int, *, url: str, body: bytes = b"") -> None:
        self.status = status
        self.url = url
        self._body = body
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


def test_duckduckgo_parser_accepts_relative_redirected_official_atom_detail() -> None:
    target = f"{ATOM_PROMOTIONS_URL}/tmobile-tuesdays-mutiny"
    html = f"""
    <html><body>
      <a class="result__a" href="/l/?kh=-1&amp;uddg={quote(target, safe='')}">
        T-Mobile Tuesdays - Mutiny | Atom, Your Ticket to More
      </a>
    </body></html>
    """
    confirmation = parse_duckduckgo_atom_confirmation_html(
        html,
        observed_at="2026-09-08T20:00:00+00:00",
    )
    assert confirmation is not None
    assert confirmation.canonical_url == target
    assert confirmation.index_provider == "DuckDuckGo public index"
    parsed = parse_current_atom_promotions_html(confirmation.synthetic_html)
    assert parsed.document_valid is True
    assert parsed.drops == ()


def test_duckduckgo_parser_accepts_protocol_relative_redirect() -> None:
    target = f"{ATOM_PROMOTIONS_URL}/get-the-new-atom-app"
    html = f"""
    <html><body>
      <a class="result__a" href="//duckduckgo.com/l/?uddg={quote(target, safe='')}">
        Get the New Atom Tickets App | Atom Tickets Promotion
      </a>
    </body></html>
    """
    confirmation = parse_duckduckgo_atom_confirmation_html(html)
    assert confirmation is not None
    assert confirmation.canonical_url == target


def test_duckduckgo_redirect_decoder_requires_exact_https_wrapper_and_one_target() -> None:
    target = f"{ATOM_PROMOTIONS_URL}/valid-offer"
    encoded = quote(target, safe="")

    assert _decode_duckduckgo_result_url(target) == target
    assert _decode_duckduckgo_result_url(f"/l/?uddg={encoded}") == target
    assert _decode_duckduckgo_result_url(f"//duckduckgo.com/l/?uddg={encoded}") == target

    rejected = (
        f"http://duckduckgo.com/l/?uddg={encoded}",
        f"https://duckduckgo.com/l/extra?uddg={encoded}",
        f"https://evil.duckduckgo.com/l/?uddg={encoded}",
        f"https://duckduckgo.com/l/?uddg={encoded}&uddg={encoded}",
        "https://duckduckgo.com/l/?uddg=",
        f"https://duckduckgo.com/l/?uddg={quote('https://example.com/promotions/fake', safe='')}",
        f"https://duckduckgo.com/l/?uddg={quote('http://www.atomtickets.com/promotions/foo', safe='')}",
        f"https://duckduckgo.com/l/?uddg={quote(ATOM_PROMOTIONS_URL + '/foo/bar', safe='')}",
    )
    for value in rejected:
        assert _decode_duckduckgo_result_url(value) == ""


def test_duckduckgo_parser_prefers_hub_and_rejects_bad_results() -> None:
    html = f"""
    <html><body>
      <a class="result__a" href="https://example.com/promotions/fake">Atom Tickets promotion</a>
      <a class="result__a" href="https://www.atomtickets.com/help/promotions">Atom Tickets promotion help</a>
      <a class="result__a" href="{ATOM_PROMOTIONS_URL}/foo/bar">Atom Tickets promotion nested</a>
      <a class="result__a" href="{ATOM_PROMOTIONS_URL}/valid-offer">Atom Tickets valid ticket offer</a>
      <a class="result__a" href="{ATOM_PROMOTIONS_URL}">Atom Tickets Promotions | Promo Hub</a>
    </body></html>
    """
    confirmation = parse_duckduckgo_atom_confirmation_html(html)
    assert confirmation is not None
    assert confirmation.canonical_url == ATOM_PROMOTIONS_URL


def test_duckduckgo_parser_requires_result_anchor_and_atom_identity() -> None:
    html = f"""
    <html><body>
      <a href="{ATOM_PROMOTIONS_URL}">Atom Tickets Promotions</a>
      <a class="result__a" href="{ATOM_PROMOTIONS_URL}/valid-offer">Unrelated page</a>
    </body></html>
    """
    assert parse_duckduckgo_atom_confirmation_html(html) is None


@pytest.mark.asyncio
async def test_client_falls_back_to_duckduckgo_when_bing_has_no_atom_result() -> None:
    bing_rss = b"""
    <rss><channel><item>
      <title>Some unrelated movie offer</title>
      <link>https://example.com/movie</link>
      <description>Nothing from Atom.</description>
    </item></channel></rss>
    """
    atom_target = f"{ATOM_PROMOTIONS_URL}/atom-rewards-refer-a-friend-offer-terms"
    ddg_html = f"""
    <html><body>
      <a class="result__a" href="/l/?kh=-1&amp;uddg={quote(atom_target, safe='')}">
        Atom Rewards Refer a Friend Offer Terms | Atom Tickets
      </a>
    </body></html>
    """.encode()
    session = FakeSession(
        [
            FakeResponse(200, url=BING_SEARCH_URL, body=bing_rss),
            FakeResponse(200, url=DUCKDUCKGO_SEARCH_URL, body=ddg_html),
        ]
    )

    confirmation = await AtomIndexedPromotionsClient(session).confirm_canonical_page()

    assert confirmation.canonical_url == atom_target
    assert confirmation.index_provider == "DuckDuckGo public index"
    assert [call["url"] for call in session.calls] == [BING_SEARCH_URL, DUCKDUCKGO_SEARCH_URL]

    bing_params = session.calls[0].get("params")
    ddg_params = session.calls[1].get("params")
    assert isinstance(bing_params, dict)
    assert isinstance(ddg_params, dict)
    assert bing_params.get("q") == ATOM_INDEX_QUERY
    assert ddg_params.get("q") == DUCKDUCKGO_INDEX_QUERY
    assert ATOM_INDEX_QUERY != DUCKDUCKGO_INDEX_QUERY


@pytest.mark.asyncio
async def test_client_stops_after_bing_success() -> None:
    bing_rss = f"""
    <rss><channel><item>
      <title>Atom Tickets Promotions | Promo Hub</title>
      <link>{ATOM_PROMOTIONS_URL}</link>
      <description>Current Atom Tickets promotions and partner offers.</description>
    </item></channel></rss>
    """.encode()
    session = FakeSession([FakeResponse(200, url=BING_SEARCH_URL, body=bing_rss)])

    confirmation = await AtomIndexedPromotionsClient(session).confirm_canonical_page()

    assert confirmation.canonical_url == ATOM_PROMOTIONS_URL
    assert confirmation.index_provider == "Bing public index"
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_client_reports_both_index_failures() -> None:
    session = FakeSession(
        [
            FakeResponse(200, url=BING_SEARCH_URL, body=b"<rss><channel/></rss>"),
            FakeResponse(200, url=DUCKDUCKGO_SEARCH_URL, body=b"<html><body>No results</body></html>"),
        ]
    )

    with pytest.raises(RuntimeError) as error:
        await AtomIndexedPromotionsClient(session).confirm_canonical_page()

    message = str(error.value)
    assert "Bing:" in message
    assert "DuckDuckGo:" in message
    assert "allowlisted official Atom promotions URL" in message
