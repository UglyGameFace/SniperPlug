from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
import asyncio
import inspect
import os
import re
import urllib.parse

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.services.walmart_cash_offers import walmart_cash_search_terms
from sniperplug.services.walmart_promo_classifier import (
    WalmartPromoScan,
    classify_walmart_api_promos,
    promo_counts_from_scans,
)


DEFAULT_ROUTE_LIMIT = 6
DETAIL_CANDIDATE_LIMIT = 24
SEARCH_CONCURRENCY = 3
DETAIL_CONCURRENCY = 4


@dataclass(frozen=True)
class WalmartApiCapability:
    mode: str
    detail_access: bool
    label: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WalmartCashBadgeEvidence:
    proof_path: str
    proof_text: str
    raw_value: str


@dataclass(frozen=True)
class WalmartDetailResult:
    candidate: SourceCandidate
    detail_attempted: bool
    detail_checked: bool
    detail_unavailable: bool
    timed_out: bool
    cash_badge_seen: bool
    cash_amount_confirmed: bool
    promo_scan: WalmartPromoScan | None
    note: str
    # Kept as always-false compatibility fields for older diagnostics/tests.
    pdp_attempted: bool = False
    pdp_checked: bool = False
    pdp_wording_seen: bool = False
    cash_detail_url: str = ""
    cash_failure_reason: str = ""


@dataclass(frozen=True)
class WalmartCashDiscovery:
    used_queries: tuple[str, ...]
    search_rows_checked: int
    detail_rows_attempted: int
    detail_rows_checked: int
    cash_badges_seen: int
    confirmed_cash_amount_rows: int
    badge_rows_without_amount: int
    other_promo_rows: int
    cash_candidates: tuple[SourceCandidate, ...]
    warnings: tuple[str, ...]
    promo_counts: dict[str, int]
    detail_unavailable: bool
    partial: bool
    capability: WalmartApiCapability
    debug_lines: tuple[str, ...]
    # Public PDP scraping is disabled. These remain zero for compatibility.
    pdp_fallback_attempted: int = 0
    pdp_fallback_checked: int = 0
    pdp_cash_wording_seen: int = 0


def detect_walmart_cash_badge(candidate: SourceCandidate) -> WalmartCashBadgeEvidence | None:
    """Detect a private Cash hint from official Walmart API row data only.

    A badge/eligibility hint is not a confirmed offer. Confirmation requires an
    explicit, sane dollar amount from the exact Walmart API row or item detail.
    """

    attrs = dict(candidate.variant_attributes or {})
    best: WalmartCashBadgeEvidence | None = None

    for key, value in attrs.items():
        path = f"variant_attributes.{key}"
        found = _badge_evidence_from_text(path, value)
        if found is not None:
            best = _choose_badge(best, found)

    for index, signal in enumerate(candidate.signals or []):
        found = _badge_evidence_from_text(f"signals[{index}]", signal)
        if found is not None:
            best = _choose_badge(best, found)

    return best


def detect_walmart_cash_badge_from_item(item: dict[str, Any]) -> WalmartCashBadgeEvidence | None:
    """Stable helper for tests/probes that inspect raw Walmart API rows."""

    best: WalmartCashBadgeEvidence | None = None
    for path, value in _walk_leaves(item):
        found = _badge_evidence_from_text(path, value)
        if found is not None:
            best = _choose_badge(best, found)
    return best


def detect_confirmed_walmart_cash_amount(
    item: dict[str, Any],
    *,
    current_price: float | None = None,
) -> bool:
    """Return True only when official Walmart API data exposes Cash amount proof."""

    return classify_walmart_api_promos(item, current_price=current_price).cash is not None


def detect_walmart_api_capability(provider: Any) -> WalmartApiCapability:
    provider = _unwrap_walmart_provider(provider)
    cfg = getattr(provider, "config", None)
    enabled = bool(getattr(cfg, "enabled", False))
    has_signed = bool(
        enabled
        and getattr(cfg, "consumer_id", None)
        and getattr(cfg, "private_key_b64", None)
    )
    has_oauth = bool(
        os.getenv("WALMART_OAUTH_ACCESS_TOKEN", "").strip()
        or os.getenv("WALMART_SOLUTION_PROVIDER_OAUTH_TOKEN", "").strip()
    )
    detail_method = callable(getattr(provider, "fetch_product_detail_payload", None))

    if has_oauth:
        return WalmartApiCapability(
            mode="oauth_solution_provider",
            detail_access=detail_method,
            label="OAuth/Solution Provider configured",
            notes=("Official search and item-detail responses are eligible for strict Cash proof.",),
        )

    if has_signed:
        return WalmartApiCapability(
            mode="signed_affiliate_api",
            detail_access=detail_method,
            label="Signed Affiliate API configured",
            notes=("Official search rows and accepted item-detail responses are checked.",),
        )

    return WalmartApiCapability(
        mode="search_only_or_disabled",
        detail_access=False,
        label="Search-only/disabled API access",
        notes=("Authenticated Walmart API access is required for strict Cash proof.",),
    )


async def run_walmart_cash_discovery(
    provider: Any,
    *,
    search: str,
    max_results: int,
    requested_by: str,
) -> WalmartCashDiscovery:
    """Find strict Walmart Cash proof using only official Walmart API responses."""

    api_provider = _unwrap_walmart_provider(provider)
    capability = detect_walmart_api_capability(api_provider)
    queries = walmart_cash_search_terms(search)
    per_route_limit = max(3, min(12, int(max_results)))
    scan_jobs = [(query, 1) for query in queries[:DEFAULT_ROUTE_LIMIT]]
    used_queries = tuple(query for query, _page in scan_jobs)
    warnings: list[str] = []
    all_candidates: list[SourceCandidate] = []

    provider_timeout = int(
        getattr(getattr(api_provider, "config", None), "timeout_seconds", 12) or 12
    )
    route_timeout = max(provider_timeout + 6, 18)
    semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)

    async def run_one_route(query: str, page: int):
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    api_provider.scan(
                        ProviderScanRequest(
                            source_key="walmart",
                            query=query.strip(),
                            max_results=per_route_limit,
                            page=page,
                            metadata={
                                "requested_by": requested_by,
                                "mode": "walmart_cash",
                                "api_truth_only": "yes",
                                "skip_scan_cache": "yes",
                                "public_pdp_fallback": "disabled",
                            },
                        )
                    ),
                    timeout=route_timeout,
                )
            except asyncio.TimeoutError:
                warnings.append(
                    f"Timed out checking official Walmart API route `{query}` page {page}."
                )
                return None
            except Exception as exc:
                warnings.append(
                    f"Skipped official Walmart API route `{query}` page {page}: "
                    f"{type(exc).__name__}"
                )
                return None

    results = await asyncio.gather(
        *(run_one_route(query, page) for query, page in scan_jobs)
    )

    for result in results:
        if result is None:
            continue
        all_candidates.extend(result.candidates)
        for warning in result.warnings:
            if warning not in warnings:
                warnings.append(warning)

    candidates = [
        _strip_search_level_cash_attrs(candidate)
        for candidate in _dedupe_candidates(all_candidates)
    ]
    candidates = _prioritize_cash_candidates(candidates)
    detail_results = await _enrich_candidates_with_detail(
        api_provider,
        candidates,
        capability=capability,
    )

    scans = [
        result.promo_scan
        for result in detail_results
        if result.promo_scan is not None
    ]
    detail_rows_attempted = sum(
        1 for result in detail_results if result.detail_attempted
    )
    detail_rows_checked = sum(
        1 for result in detail_results if result.detail_checked
    )
    timed_out = any(result.timed_out for result in detail_results)
    cash_badges_seen = sum(
        1 for result in detail_results if result.cash_badge_seen
    )
    confirmed_cash_amount_rows = sum(
        1 for result in detail_results if result.cash_amount_confirmed
    )
    badge_rows_without_amount = sum(
        1
        for result in detail_results
        if result.cash_badge_seen and not result.cash_amount_confirmed
    )
    other_promo_rows = sum(
        1
        for result in detail_results
        if _has_non_cash_promo(result.promo_scan)
    )

    for result in detail_results:
        if result.note and result.note not in warnings:
            warnings.append(result.note)

    cash_candidates = tuple(
        result.candidate
        for result in detail_results
        if _candidate_has_confirmed_cash(result.candidate)
    )

    detail_unavailable = bool(candidates) and detail_rows_checked == 0 and not cash_candidates
    partial = (
        timed_out
        or detail_unavailable
        or any("timed out" in warning.lower() for warning in warnings)
    )

    promo_counts = promo_counts_from_scans(scans)
    promo_counts.update(
        {
            "cash_badge_seen": cash_badges_seen,
            "detail_rows_attempted": detail_rows_attempted,
            "detail_rows_checked": detail_rows_checked,
            "pdp_fallback_attempted": 0,
            "pdp_fallback_checked": 0,
            "pdp_cash_wording_seen": 0,
            "confirmed_walmart_cash_amount_rows": confirmed_cash_amount_rows,
            "badge_rows_without_amount": badge_rows_without_amount,
            "other_promo_rows": other_promo_rows,
        }
    )

    return WalmartCashDiscovery(
        used_queries=used_queries,
        search_rows_checked=len(candidates),
        detail_rows_attempted=detail_rows_attempted,
        detail_rows_checked=detail_rows_checked,
        cash_badges_seen=cash_badges_seen,
        confirmed_cash_amount_rows=confirmed_cash_amount_rows,
        badge_rows_without_amount=badge_rows_without_amount,
        other_promo_rows=other_promo_rows,
        cash_candidates=cash_candidates,
        warnings=tuple(warnings[:10]),
        promo_counts=promo_counts,
        detail_unavailable=detail_unavailable,
        partial=partial,
        capability=capability,
        debug_lines=tuple(_debug_line(result) for result in detail_results[:8]),
    )


async def run_walmart_api_probe(
    provider: Any,
    *,
    query: str,
    max_results: int,
    requested_by: str,
) -> WalmartCashDiscovery:
    """Run the same official-API-only path with owner diagnostic output."""

    return await run_walmart_cash_discovery(
        provider,
        search=query,
        max_results=max(1, min(8, int(max_results))),
        requested_by=requested_by,
    )


async def _enrich_candidates_with_detail(
    provider: Any,
    candidates: list[SourceCandidate],
    *,
    capability: WalmartApiCapability,
) -> list[WalmartDetailResult]:
    provider = _unwrap_walmart_provider(provider)
    if not candidates:
        return []

    detail_method = getattr(provider, "fetch_product_detail_payload", None)
    detail_enabled = callable(detail_method) and capability.detail_access
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

    async def enrich(candidate: SourceCandidate) -> WalmartDetailResult:
        async with semaphore:
            product_id = str(
                candidate.product_id
                or candidate.sku
                or candidate.selected_offer_id
                or ""
            ).strip()
            badge_seen = _candidate_has_badge(candidate)
            enriched = replace(
                candidate,
                variant_attributes=dict(candidate.variant_attributes),
                signals=list(candidate.signals),
            )

            search_confirmed = _candidate_has_confirmed_cash(enriched)
            detail_attempted = False
            detail_checked = False
            detail_unavailable = False
            timed_out = False
            scan: WalmartPromoScan | None = None
            note = ""

            if detail_enabled and product_id:
                detail_attempted = True
                try:
                    payload = await asyncio.wait_for(
                        _call_detail_method(detail_method, product_id),
                        timeout=8,
                    )
                    item = _extract_detail_item(payload)
                    if isinstance(item, dict):
                        detail_checked = True
                        scan = classify_walmart_api_promos(
                            item,
                            current_price=candidate.current_price,
                        )
                        enriched.variant_attributes.update(scan.as_attributes())
                    else:
                        detail_unavailable = True
                        note = (
                            f"Official Walmart item detail returned no usable promo row "
                            f"for `{product_id}`."
                        )
                except asyncio.TimeoutError:
                    detail_unavailable = True
                    timed_out = True
                    note = (
                        f"Timed out checking official Walmart item detail for "
                        f"`{product_id}`."
                    )
                except Exception as exc:
                    detail_unavailable = True
                    note = (
                        f"Official Walmart item detail unavailable for `{product_id}`: "
                        f"{type(exc).__name__}"
                    )
            elif detail_enabled and not product_id:
                detail_unavailable = True
                note = "Skipped one product with no exact Walmart item ID."
            elif not search_confirmed:
                detail_unavailable = True
                note = "Official Walmart item-detail access is not available."

            detail_confirmed = bool(scan and scan.cash is not None)
            if detail_confirmed and scan and scan.cash:
                _apply_cash_truth(
                    enriched,
                    scan.cash,
                    source="affiliate_detail",
                )
            elif not search_confirmed:
                enriched.variant_attributes["cashAmountConfirmed"] = "no"

            confirmed = detail_confirmed or search_confirmed
            return WalmartDetailResult(
                candidate=enriched,
                detail_attempted=detail_attempted,
                detail_checked=detail_checked,
                detail_unavailable=detail_unavailable,
                timed_out=timed_out,
                cash_badge_seen=badge_seen,
                cash_amount_confirmed=confirmed,
                promo_scan=scan,
                note=note,
            )

    limited_candidates = candidates[:DETAIL_CANDIDATE_LIMIT]
    return list(await asyncio.gather(*(enrich(candidate) for candidate in limited_candidates)))


async def _call_detail_method(detail_method: Any, product_id: str) -> Any:
    result = detail_method(product_id)
    if inspect.isawaitable(result):
        return await result
    return result


def _apply_cash_truth(
    candidate: SourceCandidate,
    truth: Any,
    *,
    source: str,
) -> None:
    candidate.variant_attributes.update(truth.as_attributes())
    candidate.variant_attributes["cashAmountConfirmed"] = "yes"
    candidate.variant_attributes["cashProofSource"] = source
    signal = truth.signal()
    if signal not in candidate.signals:
        candidate.signals.append(signal)


def _unwrap_walmart_provider(provider: Any) -> Any:
    seen: set[int] = set()
    current = provider
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        inner = getattr(current, "inner", None)
        if inner is None or inner is current:
            break
        current = inner
    return current if current is not None else provider


def _strip_search_level_cash_attrs(candidate: SourceCandidate) -> SourceCandidate:
    """Keep strict exact API proof; strip only untrusted/search-text Cash hints."""

    badge = detect_walmart_cash_badge(candidate)
    cloned = replace(
        candidate,
        variant_attributes=dict(candidate.variant_attributes),
        signals=list(candidate.signals),
    )

    if _search_api_cash_proof_is_usable(cloned):
        cloned.variant_attributes["cashAmountConfirmed"] = "yes"
        cloned.variant_attributes["cashProofSource"] = "affiliate_search"
        cloned.variant_attributes["cashExactIdentityVerified"] = "yes"
        signal = "Walmart Cash amount confirmed from exact official API search row"
        if signal not in cloned.signals:
            cloned.signals.append(signal)
        return cloned

    for key in list(cloned.variant_attributes.keys()):
        lowered = key.lower()
        if lowered.startswith("walmartcash") or lowered.startswith("cashamountconfirmed"):
            cloned.variant_attributes.pop(key, None)
        if lowered in {
            "cashproofsource",
            "cashexactidentityverified",
            "cashdetailurl",
            "cashfailurereason",
        }:
            cloned.variant_attributes.pop(key, None)
    cloned.signals = [
        signal
        for signal in cloned.signals
        if "walmart cash api proof" not in str(signal).lower()
        and "walmart cash amount confirmed" not in str(signal).lower()
    ]

    if badge is not None:
        cloned.variant_attributes["cashBadgeSeen"] = "yes"
        cloned.variant_attributes["cashBadgeProofPath"] = badge.proof_path
        cloned.variant_attributes["cashBadgeProofText"] = badge.proof_text
        cloned.variant_attributes["cashBadgeRawValue"] = badge.raw_value
        cloned.variant_attributes["cashAmountConfirmed"] = "no"
        signal = "Walmart Cash badge seen; exact API dollar amount still required"
        if signal not in cloned.signals:
            cloned.signals.append(signal)

    return cloned


def _search_api_cash_proof_is_usable(candidate: SourceCandidate) -> bool:
    attrs = dict(candidate.variant_attributes or {})
    if str(attrs.get("walmartCashApiProof") or "").lower() != "yes":
        return False
    if str(attrs.get("walmartCashProofMode") or "") != "strict_api_field_amount":
        return False

    amount = _float_or_none(
        attrs.get("walmartCashAmount") or attrs.get("walmartCashSavings")
    )
    current_price = _float_or_none(candidate.current_price)
    if amount is None or amount <= 0 or not _amount_is_sane(amount, current_price):
        return False
    if current_price is None or current_price <= 0:
        return False

    proof_path = str(attrs.get("walmartCashProofPath") or "").strip()
    proof_text = str(attrs.get("walmartCashProofText") or "").strip()
    if not proof_path and not proof_text:
        return False

    product_id = str(candidate.product_id or candidate.sku or "").strip()
    if not product_id or not product_id.isdigit():
        return False

    url = str(candidate.direct_product_url or candidate.product_url or "").strip()
    return _url_matches_item_id(url, product_id)


def _url_matches_item_id(url: str, item_id: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if "walmart.com" not in parsed.netloc.lower():
        return False
    path = urllib.parse.unquote(parsed.path or "")
    return bool(re.search(rf"/ip/(?:[^/]+/)?{re.escape(item_id)}(?:/|$)", path))


def _prioritize_cash_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            0 if _candidate_has_confirmed_cash(candidate) else 1,
            0 if _candidate_has_badge(candidate) else 1,
        ),
    )


def _candidate_has_badge(candidate: SourceCandidate) -> bool:
    return (
        str((candidate.variant_attributes or {}).get("cashBadgeSeen") or "").lower()
        == "yes"
    )


def _candidate_has_confirmed_cash(candidate: SourceCandidate) -> bool:
    attrs = dict(candidate.variant_attributes or {})
    return (
        str(attrs.get("walmartCashApiProof") or "").lower() == "yes"
        and str(attrs.get("walmartCashProofMode") or "")
        == "strict_api_field_amount"
        and str(attrs.get("cashAmountConfirmed") or "").lower() == "yes"
        and _float_or_none(
            attrs.get("walmartCashAmount") or attrs.get("walmartCashSavings")
        )
        is not None
    )


def _has_non_cash_promo(scan: WalmartPromoScan | None) -> bool:
    if scan is None:
        return False
    return bool(
        scan.cart_promo
        or scan.onepay
        or scan.markdown
        or scan.clearance
        or scan.generic
    )


def _dedupe_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    seen: set[str] = set()
    output: list[SourceCandidate] = []

    for candidate in candidates:
        key = str(
            candidate.product_id
            or candidate.sku
            or candidate.product_url
            or candidate.title
        ).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(candidate)

    return output


def _extract_detail_item(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if payload.get("itemId") or payload.get("usItemId") or payload.get("name"):
            return payload

        for key in ("item", "product", "data"):
            child = payload.get(key)
            if isinstance(child, dict):
                found = _extract_detail_item(child)
                if found:
                    return found

        for key in ("items", "products", "itemResponse", "results"):
            child = payload.get(key)
            if isinstance(child, list):
                for item in child:
                    found = _extract_detail_item(item)
                    if found:
                        return found

    return None


def _debug_line(result: WalmartDetailResult) -> str:
    candidate = result.candidate
    attrs = dict(candidate.variant_attributes or {})
    title = " ".join(str(candidate.title or "Untitled").split())[:70]
    product_id = str(
        candidate.product_id or candidate.sku or candidate.selected_offer_id or "n/a"
    )

    bits: list[str] = []
    if result.cash_amount_confirmed:
        amount = (
            attrs.get("walmartCashAmount")
            or attrs.get("walmartCashSavings")
            or "?"
        )
        source = attrs.get("cashProofSource") or "official_api"
        bits.append(f"Walmart Cash ${amount} confirmed via {source}")
    elif result.cash_badge_seen:
        bits.append("badge hint only; no exact Cash amount")

    if result.detail_checked:
        bits.append("official item detail checked")
    elif result.detail_attempted:
        bits.append(result.note or "official item detail unavailable")

    scan = result.promo_scan
    if scan is not None:
        if scan.cart_promo:
            bits.append("cart promo separated")
        if scan.onepay:
            bits.append("OnePay separated")
        if scan.markdown:
            bits.append("markdown separated")
        if scan.clearance:
            bits.append("clearance separated")
        if scan.generic:
            bits.append("generic promo separated")

    if not bits:
        bits.append(result.note or "official API checked; no strict Cash amount")

    return f"{title} ({product_id}): " + "; ".join(bits)


def _badge_evidence_from_text(
    path: str,
    value: Any,
) -> WalmartCashBadgeEvidence | None:
    if _reject_badge_path(path):
        return None

    text = f"{path} {value}"
    lowered = str(text or "").lower()
    normalized = _norm(text)

    if any(
        term in lowered
        for term in (
            "onepay",
            "one pay",
            "cashback",
            "cash back",
            "cashrewards",
            "cash rewards",
            "credit card",
        )
    ):
        return None

    explicit_cash = "walmart cash" in lowered or "walmartcash" in normalized
    cash_reward = "cash reward" in lowered or "cashreward" in normalized
    reward_available = "reward available" in lowered or "rewardavailable" in normalized
    if not (explicit_cash or cash_reward or reward_available):
        return None

    proof_text = _clean_preview(value, 180) or _clean_preview(path, 180)
    return WalmartCashBadgeEvidence(
        proof_path=path,
        proof_text=proof_text,
        raw_value=_clean_preview(value, 220),
    )


def _reject_badge_path(path: str) -> bool:
    cleaned = str(path or "").lower().replace("[", ".").replace("]", "")
    root = cleaned.split(".", 1)[0]
    if root in {"query", "search", "request", "input", "userquery", "searchtext"}:
        return True
    if cleaned in {"title", "name", "productname", "product_name", "description"}:
        return True
    return False


def _choose_badge(
    current: WalmartCashBadgeEvidence | None,
    new: WalmartCashBadgeEvidence,
) -> WalmartCashBadgeEvidence:
    if current is None:
        return new
    return new if _badge_rank(new) > _badge_rank(current) else current


def _badge_rank(evidence: WalmartCashBadgeEvidence) -> tuple[int, int]:
    path = evidence.proof_path.lower()
    text = evidence.proof_text.lower()
    exact = (
        1
        if "walmart cash" in text
        or "walmartcash" in _norm(text)
        or "walmartcash" in _norm(path)
        else 0
    )
    return exact, len(evidence.proof_text)


def _walk_leaves(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_leaves(child, child_prefix)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            yield from _walk_leaves(child, child_prefix)
    else:
        yield prefix, value


def _amount_is_sane(amount: float, current_price: float | None) -> bool:
    if amount <= 0 or amount >= 10_000:
        return False
    if current_price is None or current_price <= 0:
        return amount <= 200
    return amount <= max(current_price * 1.10, current_price + 5.00)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, str):
        match = re.search(
            r"-?\d+(?:\.\d+)?",
            value.replace(",", "").replace("$", ""),
        )
        if not match:
            return None
        value = match.group(0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean_preview(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
