from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sniperplug.services.atom_indexed_promotions import parse_atom_indexed_fallback_marker
from sniperplug.services.movie_ticket_drops import (
    AtomPromotionParseResult,
    MovieTicketSourceState,
    normalize_text,
    parse_atom_promotions_html,
)


# Movie-ticket inventory can disappear quickly. Durable rows stay in storage for
# recovery/history, but a source that has not been successfully confirmed for two
# hours must not keep presenting old offers as current indefinitely.
MOVIE_SOURCE_CACHE_MAX_AGE_SECONDS = 2 * 60 * 60

# These are the only classifications the public multi-source runtime knows how to
# present honestly and deliver. Keep this shared with the production store so a
# parser cannot silently persist unrelated promotions as ticket alerts.
MOVIE_DELIVERABLE_CLASSIFICATIONS = frozenset({"public_reusable", "local_screening"})

_ATOM_BLOCK_PAGE_MARKERS = (
    "access denied",
    "are you a robot",
    "captcha",
    "forbidden",
    "robot or human",
    "verify you are human",
    "verify your identity",
)


def parse_current_atom_promotions_html(html_text: str) -> AtomPromotionParseResult:
    """Parse Atom while accepting its current legitimate partner-only shell.

    The original parser correctly fails closed around public reusable codes, but
    it was written when Atom always rendered both ``Film Promotions`` and
    ``Partner Promotions``. Atom can now legitimately render the official
    promotions hub with only the partner section and zero public film-ticket
    codes. Treating that page as invalid preserves old public codes forever.

    We first run the strict existing parser unchanged. The only compatibility
    case accepted here is an official-looking Atom Promotions document where the
    parser itself found the exact Partner Promotions heading but no Film
    Promotions heading. That state is valid with zero public reusable drops. A
    challenge/access-denied document is never accepted as an empty success.

    The indexed Atom fallback deliberately produces this same partner-only shell:
    it can confirm that an allowlisted official Atom promotions URL is present in
    a public index, but it cannot prove a reusable code. That lets us clear stale
    codes without upgrading search-index evidence into direct issuer proof.
    """

    parsed = parse_atom_promotions_html(html_text)
    if parsed.document_valid:
        return parsed

    page_text = normalize_text(html_text)
    if any(marker in page_text for marker in _ATOM_BLOCK_PAGE_MARKERS):
        return parsed

    partner_only_official_shell = (
        parsed.partner_section_found
        and not parsed.film_section_found
        and "atom tickets promotions" in page_text
        and "partner promotions" in page_text
    )
    if not partner_only_official_shell:
        return parsed

    return AtomPromotionParseResult(
        drops=(),
        document_valid=True,
        film_section_found=False,
        partner_section_found=True,
    )


def movie_source_cache_age_seconds(
    state: MovieTicketSourceState | None,
    *,
    fallback_seen_at: str = "",
    now: datetime | None = None,
) -> float | None:
    """Return age of the newest trustworthy source observation in seconds."""

    observed_candidates = []
    if state is not None:
        observed_candidates.append(_parse_iso(getattr(state, "last_success_at", "")))
    observed_candidates.append(_parse_iso(fallback_seen_at))
    observed = [item for item in observed_candidates if item is not None]
    if not observed:
        return None

    latest = max(observed)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    age = (current.astimezone(UTC) - latest.astimezone(UTC)).total_seconds()
    return max(0.0, age)


def movie_source_cache_is_fresh(
    state: MovieTicketSourceState | None,
    *,
    fallback_seen_at: str = "",
    now: datetime | None = None,
    max_age_seconds: float = MOVIE_SOURCE_CACHE_MAX_AGE_SECONDS,
) -> bool:
    age = movie_source_cache_age_seconds(
        state,
        fallback_seen_at=fallback_seen_at,
        now=now,
    )
    return age is not None and age <= max(1.0, float(max_age_seconds))


def movie_source_health_text(
    label: str,
    state: MovieTicketSourceState,
    *,
    official_url: str,
    now: datetime | None = None,
) -> str:
    """Render truthful source health without pretending stale cache is current."""

    fresh = movie_source_cache_is_fresh(state, now=now)
    last_success = str(getattr(state, "last_success_at", "") or "").strip()
    last_error = str(getattr(state, "last_error", "") or "").strip()
    last_modified = str(getattr(state, "last_modified", "") or "").strip()
    confirmed = _discord_time(last_success)
    indexed_state = parse_atom_indexed_fallback_marker(last_modified)

    if indexed_state is not None and last_success:
        # The normal scan loop can reuse a confirmed fallback state every minute.
        # Do not display that cache-reuse timestamp as if Bing was re-confirmed at
        # the same time. Health is based on the persisted index observation.
        indexed_age = movie_source_cache_age_seconds(
            None,
            fallback_seen_at=indexed_state.indexed_at,
            now=now,
        )
        indexed_fresh = (
            indexed_age is not None
            and indexed_age <= MOVIE_SOURCE_CACHE_MAX_AGE_SECONDS
        )
        indexed_confirmed = _discord_time(indexed_state.indexed_at)
        direct_blocked = _discord_time(indexed_state.direct_blocked_at)
        if indexed_fresh:
            health = (
                f"Indexed fallback current • direct Atom HTTP access blocked since {direct_blocked} • "
                f"official Atom promotions URL confirmed via public index {indexed_confirmed} • "
                "public reusable codes are not inferred from index snippets"
            )
            if last_error:
                health += f" • refresh error: `{_compact(last_error, 180)}`"
        else:
            health = (
                f"Indexed fallback stale • last indexed confirmation {indexed_confirmed} • "
                "cached results hidden until either direct Atom or the public index validates again"
            )
            if last_error:
                health += f" • refresh error: `{_compact(last_error, 180)}`"
    elif not last_success:
        health = "Waiting for first successful confirmation"
        if last_error:
            health += f" • last error: `{_compact(last_error, 220)}`"
    elif fresh and last_error:
        health = (
            f"Degraded • last confirmed {confirmed} • live error: "
            f"`{_compact(last_error, 220)}`"
        )
    elif fresh:
        health = f"Healthy • last confirmed {confirmed}"
    elif last_error:
        health = (
            f"Stale/unavailable • last confirmed {confirmed} • cached results hidden • "
            f"error: `{_compact(last_error, 200)}`"
        )
    else:
        health = (
            f"Stale/unconfirmed • last confirmed {confirmed} • cached results hidden "
            "until the source validates again"
        )

    return f"**{label}:** {health} • [Official page]({official_url})"


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _discord_time(value: str) -> str:
    parsed = _parse_iso(value)
    if parsed is None:
        return "never"
    return f"<t:{int(parsed.timestamp())}:R>"


def _compact(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[: max(0, int(limit))]
