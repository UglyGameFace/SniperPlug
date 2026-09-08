from __future__ import annotations

from pathlib import Path

import discord

from sniperplug.cogs.movie_result_actions import (
    annotate_movie_result_embeds,
    build_movie_result_actions_view,
    movie_result_action,
)
from sniperplug.services.fandango_movie_offers import FANDANGO_SOURCE_KEY
from sniperplug.services.gofobo_screenings import GOFOBO_SOURCE_KEY
from sniperplug.services.movie_ticket_drops import ATOM_SOURCE_KEY, MovieTicketDrop
from sniperplug.services.seeitearly_screenings import SEEITEARLY_SOURCE_KEY


ROOT = Path(__file__).resolve().parents[1]
REGISTERED = (ROOT / "sniperplug/cogs/registered_multi_source_movies.py").read_text(encoding="utf-8")
GUIDE = (ROOT / "sniperplug/cogs/movie_command_guide.py").read_text(encoding="utf-8")


def _drop(source_key: str, title: str, url: str) -> MovieTicketDrop:
    return MovieTicketDrop(
        drop_id=f"{source_key}:{title}",
        source_key=source_key,
        source_label=source_key,
        title=title,
        code="",
        classification="local_screening",
        ticket_limit=1,
        offer_url=url,
        validity_text="Current public listing",
        restrictions=(),
        raw_text=title,
    )


def test_result_action_view_gives_each_source_an_obvious_numbered_button() -> None:
    drops = [
        _drop(ATOM_SOURCE_KEY, "Atom Movie", "https://www.atomtickets.com/promotions"),
        _drop(FANDANGO_SOURCE_KEY, "Fandango Movie", "https://www.fandango.com/offers"),
        _drop(GOFOBO_SOURCE_KEY, "Gofobo Movie", "https://gofobo.com/main/local_screenings/"),
        _drop(SEEITEARLY_SOURCE_KEY, "SeeItEarly Movie", "https://seeitearly.com/movies/example"),
    ]

    view = build_movie_result_actions_view(drops)
    buttons = list(view.children)

    assert [button.label for button in buttons] == [
        "#1 Open Atom Promo",
        "#2 Open Offer",
        "#3 Check ZIP / Claim",
        "#4 Find Pass",
    ]
    assert [button.row for button in buttons] == [0, 0, 1, 1]
    assert all(button.style is discord.ButtonStyle.link for button in buttons)
    assert [button.url for button in buttons] == [drop.offer_url for drop in drops]


def test_result_action_labels_are_short_and_source_specific() -> None:
    long_title = "A Movie Title " * 30
    drop = _drop(SEEITEARLY_SOURCE_KEY, long_title, "https://seeitearly.com/movies/long-title")
    label, url, emoji = movie_result_action(drop, 8)

    assert label == "#8 Find Pass"
    assert len(label) <= 80
    assert url == drop.offer_url
    assert emoji == "🔎"


def test_result_cards_explicitly_point_to_matching_button() -> None:
    drops = [
        _drop(GOFOBO_SOURCE_KEY, "Movie One", "https://gofobo.com/one"),
        _drop(SEEITEARLY_SOURCE_KEY, "Movie Two", "https://seeitearly.com/movies/two"),
    ]
    embeds = [discord.Embed(title="Card one"), discord.Embed(title="Card two")]

    returned = annotate_movie_result_embeds(embeds, drops)

    assert returned is embeds
    assert embeds[0].fields[0].name == "👇 Result #1 action"
    assert "#1 Check ZIP / Claim" in embeds[0].fields[0].value
    assert "do not need to tap the card title" in embeds[0].fields[0].value.lower()
    assert embeds[1].fields[0].name == "👇 Result #2 action"
    assert "#2 Find Pass" in embeds[1].fields[0].value


def test_slash_latest_attaches_numbered_result_actions() -> None:
    latest_start = REGISTERED.index("async def latest(")
    latest_end = REGISTERED.index("async def scan(", latest_start)
    latest = REGISTERED[latest_start:latest_end]

    assert "annotate_movie_result_embeds" in latest
    assert "view=build_movie_result_actions_view(drops)" in latest
    assert "Use the numbered buttons" in latest


def test_permanent_panel_check_now_attaches_same_result_actions() -> None:
    start = GUIDE.index("async def send_latest_from_panel")
    end = GUIDE.index("async def send_status_from_panel", start)
    handler = GUIDE[start:end]

    assert "annotate_movie_result_embeds" in handler
    assert "view=build_movie_result_actions_view(drops)" in handler
    assert "Use the numbered buttons" in handler


def test_result_buttons_do_not_replace_existing_public_alert_feedback_controls() -> None:
    feedback = (ROOT / "sniperplug/cogs/movie_ticket_feedback.py").read_text(encoding="utf-8")

    assert "class MovieTicketFeedbackView" in feedback
    assert "Worked (0)" in feedback
    assert "Didn't work (0)" in feedback
    assert "style=discord.ButtonStyle.link" in feedback
