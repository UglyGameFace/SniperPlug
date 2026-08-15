from __future__ import annotations

import asyncio
import time
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_price_enrichment import (
    _candidate_item_id,
    _positive_number,
)
from sniperplug.services.walmart_exact_queue_runtime import (
    PAGE_CHALLENGE_CIRCUIT_COOLDOWN_SECONDS,
    _candidate_from_payload_off_loop,
    _compact_text,
    _exact_identity_failure_reason,
    _is_page_challenge_retry_kind,
    _page_challenge_circuit_open,
    _page_challenge_circuit_remaining_seconds,
    _page_circuit_state,
    _prepare_exact_candidate,
)
from sniperplug.services.walmart_exact_verification_queue import PAGE_RETRY_STATUS
from sniperplug.services.walmart_fresh_discovery_proof import (
    promote_fresh_discovery_offer_snapshot,
)
from sniperplug.services.walmart_global_offer_memory import exact_offer_identity
from sniperplug.services.walmart_request_coordinator import (
    EXACT_PRIORITY,
    walmart_request_slot,
)


# Product-page HTML is a last-resort identity fallback, not the primary deal
# scanner. Fresh catalog proof and official API/exact-search continue at normal
# exact-worker throughput. Only one HTML-capable probe may be reserved per
# minute, and the first challenge-class response opens the existing 15-minute
# provider circuit immediately. This prevents a 24/4 drain batch from sending a
# burst of page requests when the previous circuit cooldown expires.
PAGE_HTML_PROBE_INTERVAL_SECONDS = 60.0
_PAGE_HTML_NEXT_PROBE_KEY = "next_html_probe_at"


async def fetch_exact_candidate_with_page_pacing(
    provider: Any,
    claim: Any,
    *,
    timeout_seconds: float,
) -> tuple[SourceCandidate | None, str, str]:
    """Verify one queue claim API-first and pace the optional Walmart HTML fallback.

    Strict fresh catalog proof remains the cheapest path. Otherwise every claim
    receives official item-detail and, when needed, exact Affiliate-search proof
    first. HTML is attempted only when those official sources still cannot bind
    the selected seller/offer, the provider circuit is closed, and a single
    process-wide page probe token is available.

    The page probe token is reserved synchronously on the provider's existing
    circuit state, so concurrent 8/2 or 24/4 exact batches cannot race multiple
    HTML fallbacks through at once. A challenge/403/429 immediately re-opens the
    existing circuit; all other claims continue through official sources.
    """

    fresh = promote_fresh_discovery_offer_snapshot(
        str(getattr(claim, "discovery_snapshot_json", "") or ""),
        snapshot_at=getattr(claim, "discovery_snapshot_at", None),
        item_id=str(getattr(claim, "item_id", "") or ""),
        apparent_current_cents=getattr(claim, "apparent_current_cents", None),
    )
    if fresh is not None:
        return fresh, "verified", ""

    inner = getattr(provider, "inner", provider)
    official_detail_fetcher = getattr(inner, "_official_detail_payload", None)
    official_search_fetcher = getattr(inner, "_official_exact_search_payload", None)
    page_fetcher = getattr(provider, "fetch_product_detail_payload", None)
    candidate_builder = getattr(inner, "_candidate_from_item", None)
    if not callable(official_detail_fetcher) or not callable(candidate_builder):
        return None, "provider_unavailable", "exact Walmart official detail provider unavailable"

    payload: Any
    try:
        payload = await _run_exact_network_call(
            official_detail_fetcher,
            claim.item_id,
            timeout_seconds=timeout_seconds,
        )
        exact = await _candidate_from_payload_off_loop(
            candidate_builder,
            payload,
            item_id=claim.item_id,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return (
            None,
            "retry",
            f"retry_kind=outer_timeout exact Walmart official detail exceeded {float(timeout_seconds):.1f}s hard safety budget",
        )
    except Exception as error:  # noqa: BLE001 - provider failures remain retryable.
        return _provider_failure_result(provider, error, phase="official_detail")

    if exact is None:
        return (
            None,
            "retry",
            "retry_kind=candidate_build exact Walmart official detail response did not build a candidate",
        )
    if _candidate_item_id(exact) != claim.item_id:
        return None, "identity_mismatch", "exact item identity mismatch"

    merged = await asyncio.to_thread(
        _prepare_exact_candidate,
        claim,
        exact,
        payload,
    )
    identity = exact_offer_identity(merged)
    search_error_text = ""

    already_exact_search = (
        str(payload.get("sniperplugOfficialExactSource") or "").strip()
        == "affiliate_search_exact_item"
        if isinstance(payload, dict)
        else False
    )
    if identity is None and callable(official_search_fetcher) and not already_exact_search:
        try:
            search_payload = await _run_exact_network_call(
                official_search_fetcher,
                claim.item_id,
                timeout_seconds=timeout_seconds,
            )
            search_exact = await _candidate_from_payload_off_loop(
                candidate_builder,
                search_payload,
                item_id=claim.item_id,
            )
            if search_exact is not None and _candidate_item_id(search_exact) == claim.item_id:
                merged_search = await asyncio.to_thread(
                    _prepare_exact_candidate,
                    claim,
                    search_exact,
                    search_payload,
                )
                if exact_offer_identity(merged_search) is not None:
                    merged = merged_search
                    identity = exact_offer_identity(merged_search)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            search_error_text = "; exact_search=outer_timeout"
        except Exception as search_error:  # noqa: BLE001 - remains retryable diagnostic.
            search_kind = str(
                getattr(search_error, "retry_kind", "") or "api_exact_search"
            ).strip().lower()
            search_error_text = (
                f"; exact_search={search_kind}:{_compact_text(search_error, 180)}"
            )

    if identity is None and callable(page_fetcher):
        if _page_challenge_circuit_open(provider):
            return (
                None,
                PAGE_RETRY_STATUS,
                "retry_kind=page_circuit_open Walmart product-page recovery suppressed "
                "during active challenge cooldown; official exact seller/offer proof remained incomplete"
                + search_error_text,
            )

        if not reserve_page_html_probe(provider):
            return (
                None,
                PAGE_RETRY_STATUS,
                "retry_kind=page_pacing_deferred Walmart product-page recovery deferred "
                f"by the {int(PAGE_HTML_PROBE_INTERVAL_SECONDS)}s single-probe pacing guard; "
                "official exact seller/offer proof remained incomplete"
                + search_error_text,
            )

        try:
            page_payload = await asyncio.wait_for(
                page_fetcher(claim.item_id),
                timeout=max(1.0, float(timeout_seconds)),
            )
            page_exact = await _candidate_from_payload_off_loop(
                candidate_builder,
                page_payload,
                item_id=claim.item_id,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return (
                None,
                PAGE_RETRY_STATUS,
                f"retry_kind=page_timeout paced Walmart product-page recovery exceeded {float(timeout_seconds):.1f}s hard safety budget",
            )
        except Exception as error:  # noqa: BLE001 - page failures remain retryable.
            return _provider_failure_result(provider, error, phase="page_probe")

        if page_exact is None:
            return (
                None,
                "retry",
                "retry_kind=candidate_build paced Walmart product-page response did not build a candidate",
            )
        if _candidate_item_id(page_exact) != claim.item_id:
            return None, "identity_mismatch", "exact item identity mismatch"

        merged = await asyncio.to_thread(
            _prepare_exact_candidate,
            claim,
            page_exact,
            page_payload,
        )
        identity = exact_offer_identity(merged)

    if identity is None:
        return None, "incomplete_identity", _exact_identity_failure_reason(merged)
    if _positive_number(
        getattr(merged, "api_current_price", None)
        or getattr(merged, "current_price", None)
    ) is None:
        return None, "retry", "retry_kind=api_price_missing exact detail did not provide a numeric current price"
    return merged, "verified", ""


async def _run_exact_network_call(
    fetcher: Any,
    item_id: str,
    *,
    timeout_seconds: float,
) -> Any:
    """Keep direct official API helpers inside the shared exact-priority gate."""

    async with walmart_request_slot(priority=EXACT_PRIORITY):
        return await asyncio.wait_for(
            fetcher(item_id),
            timeout=max(1.0, float(timeout_seconds)),
        )


def reserve_page_html_probe(
    provider: Any,
    *,
    now_monotonic: float | None = None,
) -> bool:
    """Reserve the next single HTML fallback slot without blocking API work."""

    _owner, state = _page_circuit_state(provider, create=True)
    if state is None:
        # The production page-recovery provider always has circuit state. Keep a
        # compatibility path for provider doubles that cannot expose it.
        return True

    now_value = time.monotonic() if now_monotonic is None else float(now_monotonic)
    if _page_challenge_circuit_open(provider, now_monotonic=now_value):
        return False

    next_probe_at = float(state.get(_PAGE_HTML_NEXT_PROBE_KEY, 0.0) or 0.0)
    if next_probe_at > now_value:
        return False

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
    """A single challenge is enough to stop further HTML until cooldown expires."""

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


def _provider_failure_result(
    provider: Any,
    error: BaseException,
    *,
    phase: str,
) -> tuple[None, str, str]:
    retry_kind = str(getattr(error, "retry_kind", "") or "").strip().lower()
    if _is_page_challenge_retry_kind(retry_kind):
        _trip_page_circuit_immediately(provider)
    status = PAGE_RETRY_STATUS if retry_kind.startswith("page_") else "retry"
    kind = retry_kind or f"{phase}_provider_exception"
    return None, status, f"retry_kind={kind} {_compact_text(error, 440)}"


__all__ = [
    "PAGE_HTML_PROBE_INTERVAL_SECONDS",
    "fetch_exact_candidate_with_page_pacing",
    "page_html_probe_remaining_seconds",
    "reserve_page_html_probe",
    "_page_challenge_circuit_open",
    "_page_challenge_circuit_remaining_seconds",
]
