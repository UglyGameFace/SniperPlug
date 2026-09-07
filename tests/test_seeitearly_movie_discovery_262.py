from __future__ import annotations

import asyncio
from pathlib import Path

from sniperplug.cogs import registered_multi_source_movies as runtime
from sniperplug.cogs.movie_tickets import MovieScanOutcome
from sniperplug.cogs.registered_multi_source_movies import build_seeitearly_discovery_embed
from sniperplug.services.movie_ticket_drops import MovieTicketSourceState
from sniperplug.services.seeitearly_screenings import (
    SEEITEARLY_MAX_ACTIVE_DROPS,
    SEEITEARLY_SOURCE_KEY,
    SEEITEARLY_UPCOMING_URL,
    clean_seeitearly_title,
    extract_seeitearly_card_title,
    parse_seeitearly_upcoming_html,
    safe_seeitearly_url,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTERED = (ROOT / "sniperplug/cogs/registered_multi_source_movies.py").read_text(encoding="utf-8")


def _document(cards: str) -> str:
    return f"""
    <html>
      <head><title>SeeItEarly</title></head>
      <body>
        <h1>Releasing Soon — track before passes drop</h1>
        <div>SeeItEarly free advance movie screenings</div>
        {cards}
      </body>
    </html>
    """


def test_current_combined_card_link_extracts_only_real_movie_title() -> None:
    # Production currently exposes one link containing count + date + title + genres.
    raw = "32 free passes SEP 10 The Uprising Drama · Action · History"
    href = "/movies/the-uprising"
    assert extract_seeitearly_card_title(raw, href) == "The Uprising"

    parsed = parse_seeitearly_upcoming_html(
        _document(f'<a href="{href}">{raw}</a>')
    )
    assert parsed.document_valid is True
    assert [drop.title for drop in parsed.drops] == ["The Uprising"]
    assert "32 free screening passes" in parsed.drops[0].validity_text


def test_current_combined_card_preserves_visible_title_punctuation() -> None:
    raw = "1 free pass SEP 11 Oasis: Don't Look Back in Anger Documentary · Music"
    assert (
        extract_seeitearly_card_title(raw, "/movies/oasis-dont-look-back-in-anger")
        == "Oasis: Don't Look Back in Anger"
    )


def test_slug_boundary_preserves_hyphenated_visible_title() -> None:
    raw = "2 free passes SEP 12 Spider-Man: Brand New Day Action · Adventure"
    assert (
        extract_seeitearly_card_title(raw, "/movies/spider-man-brand-new-day")
        == "Spider-Man: Brand New Day"
    )


def test_parser_accepts_only_movie_cards_with_explicit_free_pass_count() -> None:
    parsed = parse_seeitearly_upcoming_html(
        _document(
            """
            <article>32 free passes · SEP 10 <a href="/movies/the-uprising">The Uprising</a></article>
            <article>SEP 11 <a href="/movies/paid-only">Paid Only Movie</a></article>
            """
        )
    )

    assert parsed.document_valid is True
    assert [drop.title for drop in parsed.drops] == ["The Uprising"]
    drop = parsed.drops[0]
    assert drop.source_key == SEEITEARLY_SOURCE_KEY
    assert drop.classification == "local_screening"
    assert drop.code == ""
    assert drop.offer_url == "https://seeitearly.com/movies/the-uprising"
    assert "32 free screening passes" in drop.validity_text
    assert "not the original pass issuer" in " ".join(drop.restrictions)


def test_free_pass_count_cannot_bleed_into_next_movie_card() -> None:
    parsed = parse_seeitearly_upcoming_html(
        _document(
            """
            <article>17 free passes <a href="/movies/free-title">Free Title</a></article>
            <article>Major release <a href="/movies/not-free-title">Not Free Title</a></article>
            """
        )
    )

    assert [drop.title for drop in parsed.drops] == ["Free Title"]


def test_offsite_movie_links_are_rejected_fail_closed() -> None:
    parsed = parse_seeitearly_upcoming_html(
        _document(
            """
            <article>22 free passes <a href="https://evil.example/movies/fake">Fake Movie</a></article>
            """
        )
    )

    assert parsed.document_valid is True
    assert parsed.drops == ()
    assert safe_seeitearly_url("https://evil.example/movies/fake") == ""


def test_discovery_result_count_is_bounded() -> None:
    cards = "".join(
        f'<article>{10 + index} free passes <a href="/movies/movie-{index}">Movie {index}</a></article>'
        for index in range(10)
    )
    parsed = parse_seeitearly_upcoming_html(_document(cards))

    assert parsed.document_valid is True
    assert len(parsed.drops) == SEEITEARLY_MAX_ACTIVE_DROPS == 3


def test_generic_chrome_is_not_a_movie_title() -> None:
    assert clean_seeitearly_title("Load More") == ""
    assert clean_seeitearly_title("See Screenings") == ""
    assert clean_seeitearly_title("Practical Magic 2") == "Practical Magic 2"


def test_discovery_embed_cannot_masquerade_as_direct_issuer_verification() -> None:
    drop = parse_seeitearly_upcoming_html(
        _document(
            '<article>44 free passes <a href="/movies/example-film">Example Film</a></article>'
        )
    ).drops[0]
    embed = build_seeitearly_discovery_embed(drop)
    rendered = "\n".join(
        [embed.title or "", embed.description or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
        + [embed.footer.text or ""]
    )

    assert "Supplemental" in rendered
    assert "aggregated" in rendered.lower()
    assert "original provider" in rendered.lower()
    assert "official source" not in rendered.lower()


def test_registered_runtime_scans_but_never_auto_delivers_supplemental_source() -> None:
    assert '("SeeItEarly", SEEITEARLY_SOURCE_KEY, self._scan_seeitearly_source)' in REGISTERED
    assert "SEEITEARLY_SOURCE_KEY" in REGISTERED
    start = REGISTERED.index("async def _scan_seeitearly_source")
    end = REGISTERED.index("async def _list_source_drops", start)
    supplemental_scan = REGISTERED[start:end]
    assert "replace_active_drops(SEEITEARLY_SOURCE_KEY" in supplemental_scan
    assert "record_source_success" in supplemental_scan
    assert "_deliver_drops" not in supplemental_scan
    assert "delivered_count" not in supplemental_scan
    assert "                    0," in supplemental_scan


def test_automatic_discovery_is_paced_but_manual_refresh_bypasses_pacing(monkeypatch) -> None:
    class _Store:
        async def list_active_drops(self, *, limit: int):
            assert limit == 100
            return []

    async def run() -> None:
        cog = object.__new__(runtime.MovieTicketsCog)
        cog.store = _Store()
        cog._source_failure_counts = {}
        cog._source_retry_after = {}
        cog._source_access_blocked = set()
        cog._last_total_failure_log_monotonic = 0.0
        cog._last_source_scan_errors = ()
        cog._seeitearly_next_auto_scan_monotonic = 0.0

        calls = {"atom": 0, "fandango": 0, "gofobo": 0, "seeitearly": 0}

        def scanner(name: str):
            async def _scan(*, target_guild_id=None):
                del target_guild_id
                calls[name] += 1
                state = MovieTicketSourceState(source_key=name, last_success_at="2026-09-07T20:00:00+00:00")
                return MovieScanOutcome(False, 0, 0, state)

            return _scan

        cog._scan_atom_source = scanner("atom")
        cog._scan_fandango_source = scanner("fandango")
        cog._scan_gofobo_source = scanner("gofobo")
        cog._scan_seeitearly_source = scanner("seeitearly")

        monkeypatch.setattr(runtime.time, "monotonic", lambda: 1000.0)
        await cog._scan_official_source(target_guild_id=None)
        await cog._scan_official_source(target_guild_id=None)
        await cog._scan_official_source(target_guild_id=123)

        assert calls == {"atom": 3, "fandango": 3, "gofobo": 3, "seeitearly": 2}
        assert cog._seeitearly_next_auto_scan_monotonic == 1000.0 + runtime.SEEITEARLY_AUTO_POLL_SECONDS

    asyncio.run(run())


def test_registered_status_and_sources_describe_expanded_source_truth() -> None:
    assert "SEEITEARLY_AUTO_POLL_SECONDS = 10 * 60" in REGISTERED
    assert '@app_commands.command(\n        name="status"' in REGISTERED
    assert 'name="Connected source health"' in REGISTERED
    assert 'name="Check cadence"' in REGISTERED
    assert "Check Now/manual refresh can check sooner" in REGISTERED
    assert '@app_commands.command(\n        name="sources"' in REGISTERED
    assert "Direct monitored + automatic alerts" in REGISTERED
    assert "Automated supplemental discovery" in REGISTERED
    assert "Extra manual directories" in REGISTERED
    assert "Private/account sources not scraped" in REGISTERED
    assert "AdvanceScreenings" in REGISTERED
    assert "1iota" in REGISTERED


def test_public_upcoming_url_is_https_seeitearly() -> None:
    assert SEEITEARLY_UPCOMING_URL == "https://seeitearly.com/upcoming"
