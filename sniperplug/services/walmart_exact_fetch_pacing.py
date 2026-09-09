from __future__ import annotations

import time
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart_exact_lookup import (
    fetch_exact_affiliate_product_lookup_payload,
)
from sniperplug.services.walmart_exact_price_enrichment import _trusted_reference
from sniperplug.services.walmart_exact_queue_runtime import (
    PAGE_CHALLENGE_CIRCUIT_COOLDOWN_SECONDS,
    _PAGE_CIRCUIT_STATE_ATTR,
    _fetch_exact_candidate_off_loop,
    _is_fresh_snapshot_candidate,
    _is_page_challenge_retry_kind,
    _page_challenge_circuit_open,
    _page_challenge_circuit_remaining_seconds,
    _page_circuit_state,
    _retry_kind_from_error,
)
from sniperplug.services.walmart_exact_verification_queue import PAGE_RETRY_STATUS


# Product-page HTML is a last-resort identity fallback, not the primary deal
# scanner. Fresh catalog proof and official API exact lookup keep using the
# canonical exact verifier. Only one claim per provider/process may advance from
# API-only proof to an HTML-capable pass each minute. A first challenge-class
# result opens the existing 15-minute circuit immediately.
PAGE_HTML_PROBE_INTERVAL_SECONDS = 60.0
_PAGE_HTML_NEXT_PROBE_KEY = "next_html_probe_at"


class _ApiOnlyInnerView:
    """Delegate exact provider helpers while exposing an isolated open circuit."""

    def __init__(self, inner: Any, *, hold_seconds: float) -> None:
        self._inner = inner
        setattr(
            self,
            _PAGE_CIRCUIT_STATE_ATTR,
            {
                "events": [],
                "until": time.monotonic() + max(60.0, float(hold_seconds)),
            },
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _ApiOnlyProviderView:
    """Run the canonical verifier with HTML suppressed without mutating live state."""

    def __init__(self, provider: Any, *, hold_seconds: float) -> None:
        self._provider = provider
        self.inner = _ApiOnlyInnerView(
            getattr(provider, "inner", provider),
            hold_seconds=hold_seconds,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


class _ExactSearchOnlyInnerView(_ApiOnlyInnerView):
    """Make exact Affiliate Product Lookup the canonical verifier's detail source.

    The class name is retained for compatibility with the already-tested #257
    wrapper shape. Production no longer treats a numeric item ID as free-text
    Search API input.
    """

    async def _official_detail_payload(self, item_id: str) -> dict:
        try:
            return await fetch_exact_affiliate_product_lookup_payload(
                self._inner,
                item_id,
            )
        except Exception as lookup_error:  # noqa: BLE001 - compatibility path only.
            lookup_kind = str(
                getattr(lookup_error, "retry_kind", "") or ""
            ).strip().lower()
            if lookup_kind != "api_exact_lookup_unavailable":
                raise

            # Test/plugin providers created before #274 may expose only the old
            # exact-search hook. Keep those adapters functional, but production's
            # Walmart provider has detail_url/_request_json and never takes this
            # branch.
            search_fetcher = getattr(
                self._inner,
                "_official_exact_search_payload",
                None,
            )
            if not callable(search_fetcher):
                raise lookup_error
            return await search_fetcher(item_id)


class _ExactSearchOnlyProviderView:
    """Run one exact-lookup recovery pass with page HTML impossible."""

    def __init__(self, provider: Any, *, hold_seconds: float) -> None:
        self._provider = provider
        self.inner = _ExactSearchOnlyInnerView(
            getattr(provider, "inner", provider),
            hold_seconds=hold_seconds,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


class _NetworkRecoveryClaimView:
    """Hide fresh proof so a recovery pass must use the requested official source."""

    discovery_snapshot_json = ""
    discovery_snapshot_at = None

    def __init__(self, claim: Any) -> None:
        self._claim = claim

    def __getattr__(self, name: str) -> Any:
        return getattr(self._claim, name)


async def fetch_exact_candidate_with_page_pacing(
    provider: Any,
    claim: Any,
    *,
    timeout_seconds: float,
) -> tuple[SourceCandidate | None, str, str]:
    """Keep fresh/API proof fast while allowing only a paced HTML fallback.

    The existing exact verifier remains the only proof implementation. Each claim
    first runs with HTML suppressed. Valid fresh catalog proof or a complete
    official item-detail result returns without page traffic.

    Production can receive an exact-detail row with valid seller/offer identity
    but no numeric current price. #270 proved that the old recovery then sent the
    Walmart item ID to the relevance Search API and overwhelmingly got
    ``api_exact_search_no_match``. Recovery now uses Affiliate Product Lookup's
    exact ``ids`` parameter, verifies the returned item ID, and sends that payload
    through the same canonical verifier. No search neighbor can donate proof.

    A strict fresh catalog snapshot can also prove current selected-offer price
    while lacking a trusted Walmart was/regular/original reference. The same
    Product Lookup recovery may enrich that candidate, but failure never
    downgrades the already-valid fresh proof or sends it to HTML.

    When the real HTML circuit is already open, price/reference recovery remains
    active; the circuit suppresses HTML, not official Product Lookup. When the
    real circuit is closed, only a claim whose API-only result says
    ``page_circuit_open`` solely because the synthetic view suppressed HTML may
    advance to a real page-capable pass.

    A synchronous provider-state reservation allows at most one such real pass
    per minute. Therefore concurrent 8/2 or 24/4 batches cannot release several
    Walmart product-page requests when the real circuit cooldown expires. If the
    paced probe itself receives a challenge/403/429, the real provider circuit is
    opened for the existing fifteen-minute cooldown immediately.
    """

    real_circuit_open = _page_challenge_circuit_open(provider)
    primary_provider = (
        provider
        if real_circuit_open
        else _ApiOnlyProviderView(
            provider,
            hold_seconds=max(60.0, float(timeout_seconds) * 2.0),
        )
    )
    candidate, status, error = await _fetch_exact_candidate_off_loop(
        primary_provider,
        claim,
        timeout_seconds=timeout_seconds,
    )
    candidate, status, error = await _recover_api_price_or_reference(
        provider,
        claim,
        candidate=candidate,
        status=status,
        error=error,
        timeout_seconds=timeout_seconds,
    )
    retry_kind = _retry_kind_from_error(error)

    # A real open circuit is an API-only mode. Never reserve or call HTML from it,
    # even if identity remains incomplete after official API recovery.
    if real_circuit_open:
        return candidate, status, error

    # Complete proof, true API failures, and strict identity failures keep their
    # canonical result. Only the synthetic API-only page-circuit result means an
    # HTML fallback is still needed.
    if candidate is not None or retry_kind != "page_circuit_open":
        return candidate, status, error

    # Another concurrent claim may have tripped the real circuit while this API
    # pass was in flight. Never reserve or call HTML after that happens.
    if _page_challenge_circuit_open(provider):
        return None, PAGE_RETRY_STATUS, (
            "retry_kind=page_circuit_open Walmart product-page recovery suppressed "
            "during active challenge cooldown; official exact seller/offer proof remained incomplete"
        )

    if not reserve_page_html_probe(provider):
        return None, PAGE_RETRY_STATUS, (
            "retry_kind=page_pacing_deferred Walmart product-page recovery deferred "
            f"by the {int(PAGE_HTML_PROBE_INTERVAL_SECONDS)}s single-probe pacing guard; "
            "official exact seller/offer proof remained incomplete"
        )

    # The real canonical pass may repeat the official lookup before its HTML
    # fallback. That happens for at most one claim per minute and deliberately
    # avoids maintaining a second seller/offer proof implementation here.
    candidate, status, error = await _fetch_exact_candidate_off_loop(
        provider,
        claim,
        timeout_seconds=timeout_seconds,
    )
    retry_kind = _retry_kind_from_error(error)
    if _is_page_challenge_retry_kind(retry_kind):
        _trip_page_circuit_immediately(provider)
    return candidate, status, error


async def _recover_api_price_or_reference(
    provider: Any,
    claim: Any,
    *,
    candidate: SourceCandidate | None,
    status: str,
    error: str,
    timeout_seconds: float,
) -> tuple[SourceCandidate | None, str, str]:
    """Recover exact price/reference without weakening a valid primary result."""

    retry_kind = _retry_kind_from_error(error)

    # A detail payload with exact identity but no price is not grounds for HTML.
    # Give Affiliate Product Lookup one exact-ID-only chance to supply the missing
    # current price through the canonical verifier.
    if candidate is None and retry_kind == "api_price_missing":
        recovered = await _try_official_exact_search_recovery(
            provider,
            claim,
            timeout_seconds=timeout_seconds,
        )
        if recovered[0] is not None:
            return recovered
        recovery_kind = _retry_kind_from_error(recovered[2])
        if recovery_kind == "unclassified":
            recovery_kind = f"status_{str(recovered[1] or 'unknown').strip().lower()}"
        return candidate, status, f"{error}; recovery_kind={recovery_kind}"

    # A strict fresh snapshot is already valid current-price proof. If it lacks a
    # trusted Walmart reference, Product Lookup may enrich it with a same-item
    # was/regular/original price. Failure to find one must never downgrade the
    # already-valid candidate or send the request to HTML.
    if (
        candidate is not None
        and _is_fresh_snapshot_candidate(candidate)
        and _trusted_reference(candidate) is None
    ):
        recovered = await _try_official_exact_search_recovery(
            provider,
            claim,
            timeout_seconds=timeout_seconds,
        )
        if recovered[0] is not None and _trusted_reference(recovered[0]) is not None:
            return recovered

    return candidate, status, error


async def _try_official_exact_search_recovery(
    provider: Any,
    claim: Any,
    *,
    timeout_seconds: float,
) -> tuple[SourceCandidate | None, str, str]:
    """Run one fail-closed exact-ID Affiliate Product Lookup recovery pass."""

    inner = getattr(provider, "inner", provider)
    lookup_available = bool(
        str(getattr(inner, "detail_url", "") or "").strip()
        and callable(getattr(inner, "_request_json", None))
    )
    compatibility_search_available = callable(
        getattr(inner, "_official_exact_search_payload", None)
    )
    if not lookup_available and not compatibility_search_available:
        return (
            None,
            "provider_unavailable",
            "retry_kind=api_exact_lookup_unavailable official Walmart exact Product Lookup unavailable",
        )

    recovery_provider = _ExactSearchOnlyProviderView(
        provider,
        hold_seconds=max(60.0, float(timeout_seconds) * 2.0),
    )
    return await _fetch_exact_candidate_off_loop(
        recovery_provider,
        _NetworkRecoveryClaimView(claim),
        timeout_seconds=timeout_seconds,
    )


def reserve_page_html_probe(
    provider: Any,
    *,
    now_monotonic: float | None = None,
) -> bool:
    """Reserve the next single HTML-capable exact pass without blocking API work."""

    _owner, state = _page_circuit_state(provider, create=True)
    if state is None:
        # Production's WalmartPageRecoveryProvider exposes circuit state. Keep a
        # compatibility path for test/plugin providers without page recovery.
        return True

    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    if _page_challenge_circuit_open(provider, now_monotonic=now_value):
        return False

    next_probe_at = float(state.get(_PAGE_HTML_NEXT_PROBE_KEY, 0.0) or 0.0)
    if next_probe_at > now_value:
        return False

    # There is no await between reading and writing this process-local provider
    # state, so asyncio tasks cannot double-reserve the same slot.
    state[_PAGE_HTML_NEXT_PROBE_KEY] = now_value + PAGE_HTML_PROBE_INTERVAL_SECONDS
    return True


def page_html_probe_remaining_seconds(
    provider: Any,
    *,
    now_monotonic: float | None = None,
) -> int:
    _owner, state = _page_circuit_state(provider, create=False)
    if state is None:
        return 0
    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    remaining = float(state.get(_PAGE_HTML_NEXT_PROBE_KEY, 0.0) or 0.0) - now_value
    return max(0, int(round(remaining)))


def _trip_page_circuit_immediately(
    provider: Any,
    *,
    now_monotonic: float | None = None,
) -> None:
    """Make one paced challenge sufficient to hold further Walmart HTML."""

    _owner, state = _page_circuit_state(provider, create=True)
    if state is None:
        return
    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    until = now_value + PAGE_CHALLENGE_CIRCUIT_COOLDOWN_SECONDS
    state["events"] = [now_value]
    state["until"] = max(float(state.get("until", 0.0) or 0.0), until)
    state[_PAGE_HTML_NEXT_PROBE_KEY] = max(
        float(state.get(_PAGE_HTML_NEXT_PROBE_KEY, 0.0) or 0.0),
        state["until"],
    )


__all__ = [
    "PAGE_HTML_PROBE_INTERVAL_SECONDS",
    "fetch_exact_candidate_with_page_pacing",
    "page_html_probe_remaining_seconds",
    "reserve_page_html_probe",
    "_page_challenge_circuit_open",
    "_page_challenge_circuit_remaining_seconds",
]
