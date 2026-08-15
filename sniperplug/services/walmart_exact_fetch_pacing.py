from __future__ import annotations

import time
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_queue_runtime import (
    PAGE_CHALLENGE_CIRCUIT_COOLDOWN_SECONDS,
    _PAGE_CIRCUIT_STATE_ATTR,
    _fetch_exact_candidate_off_loop,
    _is_page_challenge_retry_kind,
    _page_challenge_circuit_open,
    _page_challenge_circuit_remaining_seconds,
    _page_circuit_state,
    _retry_kind_from_error,
)
from sniperplug.services.walmart_exact_verification_queue import PAGE_RETRY_STATUS


# Product-page HTML is a last-resort identity fallback, not the primary deal
# scanner. Fresh catalog proof and official API/exact-search keep using the
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


async def fetch_exact_candidate_with_page_pacing(
    provider: Any,
    claim: Any,
    *,
    timeout_seconds: float,
) -> tuple[SourceCandidate | None, str, str]:
    """Keep fresh/API proof fast while allowing only a paced HTML fallback.

    The existing exact verifier remains the only proof implementation. Each claim
    first runs through an isolated API-only view. Valid fresh catalog proof or a
    complete official item-detail/exact-search result returns immediately. Only a
    claim whose canonical result says ``page_circuit_open`` solely because the
    API-only view intentionally suppressed HTML is eligible for the real provider
    pass.

    A synchronous provider-state reservation allows at most one such real pass
    per minute. Therefore concurrent 8/2 or 24/4 batches cannot release several
    Walmart product-page requests when the real circuit cooldown expires. If the
    paced probe itself receives a challenge/403/429, the real provider circuit is
    opened for the existing fifteen-minute cooldown immediately.
    """

    # If Walmart HTML is already held, use the canonical verifier directly. Its
    # existing open-circuit path uses fresh proof / official exact sources only.
    if _page_challenge_circuit_open(provider):
        return await _fetch_exact_candidate_off_loop(
            provider,
            claim,
            timeout_seconds=timeout_seconds,
        )

    api_only_provider = _ApiOnlyProviderView(
        provider,
        hold_seconds=max(60.0, float(timeout_seconds) * 2.0),
    )
    candidate, status, error = await _fetch_exact_candidate_off_loop(
        api_only_provider,
        claim,
        timeout_seconds=timeout_seconds,
    )

    # Fresh catalog proof, complete official API proof, true API failures, and
    # strict identity failures all keep their canonical result. Only the
    # synthetic API-only page-circuit result means an HTML fallback is needed.
    if candidate is not None or _retry_kind_from_error(error) != "page_circuit_open":
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
