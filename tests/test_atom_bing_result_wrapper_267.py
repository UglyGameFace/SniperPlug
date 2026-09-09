from __future__ import annotations

import base64
from xml.sax.saxutils import escape

from sniperplug.services.atom_indexed_promotions import (
    normalize_bing_atom_result_url,
    parse_bing_atom_confirmation_rss,
)
from sniperplug.services.movie_ticket_drops import ATOM_PROMOTIONS_URL


def _bing_wrapper(destination: str, *, host: str = "www.bing.com", path: str = "/ck/a") -> str:
    encoded = base64.urlsafe_b64encode(destination.encode("utf-8")).decode("ascii").rstrip("=")
    return f"https://{host}{path}?!&&p=test&u=a1{encoded}&ntb=1"


def _rss_item(*, title: str, link: str, description: str) -> str:
    return (
        "<item>"
        f"<title>{escape(title)}</title>"
        f"<link>{escape(link)}</link>"
        f"<description>{escape(description)}</description>"
        "</item>"
    )


def test_bing_wrapper_decoder_recovers_allowlisted_atom_destination() -> None:
    destination = f"{ATOM_PROMOTIONS_URL}/atom-rewards-refer-a-friend-offer-terms"
    wrapped = _bing_wrapper(destination)

    assert normalize_bing_atom_result_url(wrapped) == destination
    assert normalize_bing_atom_result_url(destination) == destination


def test_bing_wrapper_decoder_fails_closed_outside_exact_wrapper_and_atom_allowlists() -> None:
    rejected = (
        _bing_wrapper("https://example.com/promotions/fake"),
        _bing_wrapper("https://www.atomtickets.com/help/promotions"),
        _bing_wrapper("https://www.atomtickets.com/promotions/foo/bar"),
        _bing_wrapper("http://www.atomtickets.com/promotions/foo"),
        _bing_wrapper(f"{ATOM_PROMOTIONS_URL}/foo", host="evil.bing.com"),
        _bing_wrapper(f"{ATOM_PROMOTIONS_URL}/foo", path="/search"),
        "https://www.bing.com/ck/a?u=https%3A%2F%2Fwww.atomtickets.com%2Fpromotions",
        "https://www.bing.com/ck/a?u=a1not*base64",
        "https://www.bing.com/ck/a?u=a1",
    )
    for value in rejected:
        assert normalize_bing_atom_result_url(value) == ""


def test_bing_rss_accepts_current_tracker_wrapped_atom_detail_result() -> None:
    destination = f"{ATOM_PROMOTIONS_URL}/tmobile-tuesdays-mutiny"
    rss = (
        "<rss><channel>"
        + _rss_item(
            title="T-Mobile Tuesdays - Mutiny | Atom, Your Ticket to More",
            link=_bing_wrapper(destination),
            description="Official Atom Tickets promotional ticket offer terms.",
        )
        + "</channel></rss>"
    )

    confirmation = parse_bing_atom_confirmation_rss(
        rss,
        observed_at="2026-09-08T20:30:00+00:00",
    )
    assert confirmation is not None
    assert confirmation.canonical_url == destination


def test_bing_rss_prefers_wrapped_canonical_hub_over_wrapped_detail() -> None:
    detail = f"{ATOM_PROMOTIONS_URL}/atom-rewards-refer-a-friend-offer-terms"
    rss = (
        "<rss><channel>"
        + _rss_item(
            title="Atom Rewards Refer a Friend Offer Terms",
            link=_bing_wrapper(detail),
            description="Official Atom Tickets promotional offer terms.",
        )
        + _rss_item(
            title="Atom Tickets Promotions | Promo Hub",
            link=_bing_wrapper(ATOM_PROMOTIONS_URL),
            description="Current Atom Tickets promotions and partner offers.",
        )
        + "</channel></rss>"
    )

    confirmation = parse_bing_atom_confirmation_rss(rss)
    assert confirmation is not None
    assert confirmation.canonical_url == ATOM_PROMOTIONS_URL


def test_bing_rss_rejects_wrapped_non_atom_destination_even_with_atom_identity_text() -> None:
    rss = (
        "<rss><channel>"
        + _rss_item(
            title="Atom Tickets promotion",
            link=_bing_wrapper("https://example.com/promotions/fake"),
            description="Atom ticket offer.",
        )
        + "</channel></rss>"
    )

    assert parse_bing_atom_confirmation_rss(rss) is None
