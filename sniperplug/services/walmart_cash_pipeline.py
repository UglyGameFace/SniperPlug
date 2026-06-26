from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
import asyncio
import inspect
import os
import re

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest
from sniperplug.services.walmart_cash_offers import walmart_cash_search_terms
from sniperplug.services.walmart_pdp_cash_proof import (
    WalmartPdpCashProof,
    candidate_pdp_url,
    check_walmart_pdp_cash_truth,
    walmart_pdp_cash_proof_from_html,
)
from sniperplug.services.walmart_promo_classifier import (
    WalmartPromoScan,
    classify_walmart_api_promos,
    promo_counts_from_scans,
)


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
    pdp_fallback_attempted: int = 0
    pdp_fallback_checked: int = 0
    pdp_cash_wording_seen: int = 0


def detect_walmart_cash_badge(candidate: SourceCandidate) -> WalmartCashBadgeEvidence | None:
    """Detect a private Cash Finder badge candidate from API row data only.

    This intentionally does not read the user's search query or the product title.
    A badge is a trigger for exact product detail/PDP enrichment, not a confirmed
    Walmart Cash offer.
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


def detect_confirmed_walmart_cash_amount(item: dict[str, Any], *, current_price: float | None = None) -> bool:
    """Return True only when detail/PDP data exposes explicit Walmart Cash amount proof."""

    return classify_walmart_api_promos(item, current_price=current_price).cash is not None


def detect_walmart_api_capability(provider: Any) -> WalmartApiCapability:
    provider = _unwrap_walmart_provider(provider)
    cfg = getattr(provider, "config", None)
    enabled = bool(getattr(cfg, "enabled", False))
    has_signed = bool(enabled and getattr(cfg, "consumer_id", None) and getattr(cfg, "private_key_b64", None))
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
            notes=("Full promo detail may be available when Walmart grants the app access.",),
        )

    if has_signed:
        return WalmartApiCapability(
            mode="signed_affiliate_api",
            detail_access=detail_method,
            label="Signed Affiliate API configured",
            notes=("Search access is configured. Detail promo access depends on Walmart accepting the detail endpoint.",),
        )

    return WalmartApiCapability(
        mode="search_only_or_disabled",
        detail_access=False,
        label="Search-only/disabled API access",
        notes=("Walmart Cash cannot be proven until authenticated Walmart API access exposes promo detail.",),
    )


async def run_walmart_cash_discovery(
    provider: Any,
    *,
    search: str,
    max_results: int,
    requested_by: str,
) -> WalmartCashDiscovery:
    # Cash Finder needs live Walmart API truth, not cached normalized scan rows.
    # The normal bot registers CachedWalmartProvider for public deal scans, so
    # unwrap it here to avoid DB/cache work blocking the command response and to
    # expose the real provider's signed config/detail method to the probe.
    api_provider = _unwrap_walmart_provider(provider)
    capability = detect_walmart_api_capability(api_provider)
    queries = walmart_cash_search_terms(search)
    per_route_limit = max(3, min(12, int(max_results)))
    scan_jobs = [(query, 1) for query in queries[:2]]
    used_queries = tuple(query for query, _page in scan_jobs)
    warnings: list[str] = []
    all_candidates: list[SourceCandidate] = []

    provider_timeout = int(getattr(getattr(api_provider, "config", None), "timeout_seconds", 12) or 12)
    route_timeout = max(provider_timeout + 6, 18)
    semaphore = asyncio.Semaphore(2)

    async def run_one_route(query: str, page: int):
        async with semaphore:
            try:
                result = await asyncio.wait_for(
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
                            },
                        )
                    ),
                    timeout=route_timeout,
                )
                return result
            except asyncio.TimeoutError:
                warnings.append(f"Timed out checking `{query}` page {page}; partial result, not a proven no-offer result.")
                return None
            except Exception as exc:
                warnings.append(f"Skipped `{query}` page {page}: {type(exc).__name__}")
                return None

    results = await asyncio.gather(*(run_one_route(query, page) for query, page in scan_jobs))

    for result in results:
        if result is None:
            continue
        all_candidates.extend(result.candidates)
        warnings.extend(w for w in result.warnings if w not in warnings)

    candidates = [_strip_search_level_cash_attrs(candidate) for candidate in _dedupe_candidates(all_candidates)]
    candidates = _prioritize_badge_candidates(candidates)
    detail_results = await _enrich_candidates_with_detail(api_provider, candidates, capability=capability)

    scans = [result.promo_scan for result in detail_results if result.promo_scan is not None]
    detail_rows_attempted = sum(1 for result in detail_results if result.detail_attempted)
    detail_rows_checked = sum(1 for result in detail_results if result.detail_checked)
    pdp_fallback_attempted = sum(1 for result in detail_results if result.pdp_attempted)
    pdp_fallback_checked = sum(1 for result in detail_results if result.pdp_checked)
    pdp_cash_wording_seen = sum(1 for result in detail_results if result.pdp_wording_seen)
    detail_unavailable = bool(candidates) and detail_rows_checked == 0 and pdp_fallback_checked == 0
    timed_out = any(result.timed_out for result in detail_results)
    cash_badges_seen = sum(1 for result in detail_results if result.cash_badge_seen)
    confirmed_cash_amount_rows = sum(1 for result in detail_results if result.cash_amount_confirmed)
    badge_rows_without_amount = sum(
        1
        for result in detail_results
        if (result.cash_badge_seen or result.pdp_wording_seen) and not result.cash_amount_confirmed
    )
    other_promo_rows = sum(1 for result in detail_results if _has_non_cash_promo(result.promo_scan))

    for result in detail_results:
        if result.note and result.note not in warnings:
            warnings.append(result.note)

    cash_candidates = tuple(
        result.candidate
        for result in detail_results
        if str(result.candidate.variant_attributes.get("walmartCashApiProof") or "").lower() == "yes"
    )

    partial = timed_out or detail_unavailable or any("partial result" in warning.lower() for warning in warnings)
    promo_counts = promo_counts_from_scans(scans)
    promo_counts.update(
        {
            "cash_badge_seen": cash_badges_seen,
            "detail_rows_attempted": detail_rows_attempted,
            "detail_rows_checked": detail_rows_checked,
            "pdp_fallback_attempted": pdp_fallback_attempted,
            "pdp_fallback_checked": pdp_fallback_checked,
            "pdp_cash_wording_seen": pdp_cash_wording_seen,
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
        warnings=tuple(warnings[:8]),
        promo_counts=promo_counts,
        detail_unavailable=detail_unavailable,
        partial=partial,
        capability=capability,
        debug_lines=tuple(_debug_line(result) for result in detail_results[:8]),
        pdp_fallback_attempted=pdp_fallback_attempted,
        pdp_fallback_checked=pdp_fallback_checked,
        pdp_cash_wording_seen=pdp_cash_wording_seen,
    )


async def run_walmart_api_probe(
    provider: Any,
    *,
    query: str,
    max_results: int,
    requested_by: str,
) -> WalmartCashDiscovery:
    # Probe uses the same real pipeline so diagnostics cannot lie.
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
    pdp_fetcher = getattr(provider, "fetch_walmart_pdp_html", None)
    detail_enabled = callable(detail_method) and capability.detail_access
    semaphore = asyncio.Semaphore(3)

    async def enrich(candidate: SourceCandidate) -> WalmartDetailResult:
        async with semaphore:
            product_id = str(candidate.product_id or candidate.sku or candidate.selected_offer_id or "").strip()
            badge_seen = _candidate_has_badge(candidate)
            enriched = replace(candidate, variant_attributes=dict(candidate.variant_attributes), signals=list(candidate.signals))

            detail_attempted = False
            detail_checked = False
            detail_unavailable = False
            timed_out = False
            scan: WalmartPromoScan | None = None
            note = ""

            if detail_enabled and product_id:
                detail_attempted = True
                try:
                    payload = await asyncio.wait_for(_call_detail_method(detail_method, product_id), timeout=8)
                    item = _extract_detail_item(payload)
                    if isinstance(item, dict):
                        detail_checked = True
                        scan = classify_walmart_api_promos(item, current_price=candidate.current_price)
                        enriched.variant_attributes.update(scan.as_attributes())
                    else:
                        detail_unavailable = True
                        note = f"Detail endpoint returned no usable product promo row for `{product_id}`."
                except asyncio.TimeoutError:
                    detail_unavailable = True
                    timed_out = True
                    note = f"Timed out checking detail promo proof for `{product_id}`."
                except Exception as exc:
                    detail_unavailable = True
                    note = f"Detail promo proof unavailable for `{product_id}`: {type(exc).__name__}"
            elif detail_enabled and not product_id:
                detail_unavailable = True
                note = "Skipped one product with no Walmart product ID for detail proof."
            else:
                detail_unavailable = True
                note = "Walmart did not expose full promo detail through the current API access."

            confirmed = bool(scan and scan.cash is not None)
            if confirmed and scan and scan.cash:
                _apply_cash_truth(enriched, scan.cash, source="affiliate_detail")
            elif badge_seen:
                enriched.variant_attributes["cashAmountConfirmed"] = "no"

            pdp_result = WalmartPdpCashProof(False, False, False, None)
            if not confirmed and badge_seen:
                pdp_result = await _check_candidate_pdp(candidate, pdp_fetcher=pdp_fetcher)
                if pdp_result.attempted:
                    enriched.variant_attributes["cashDetailUrl"] = pdp_result.url
                if pdp_result.cash_truth is not None:
                    scan = _scan_with_cash(scan, pdp_result.cash_truth)
                    _apply_cash_truth(enriched, pdp_result.cash_truth, source="walmart_pdp", detail_url=pdp_result.url)
                    confirmed = True
                    note = ""
                else:
                    enriched.variant_attributes["cashAmountConfirmed"] = "no"
                    if pdp_result.failure_reason:
                        enriched.variant_attributes["cashFailureReason"] = pdp_result.failure_reason
                    if pdp_result.failure_reason and not note:
                        note = pdp_result.failure_reason

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
                pdp_attempted=pdp_result.attempted,
                pdp_checked=pdp_result.checked,
                pdp_wording_seen=pdp_result.wording_seen,
                cash_detail_url=pdp_result.url,
                cash_failure_reason=pdp_result.failure_reason,
            )

    return list(await asyncio.gather(*(enrich(candidate) for candidate in candidates[:12])))


async def _check_candidate_pdp(candidate: SourceCandidate, *, pdp_fetcher: Any) -> WalmartPdpCashProof:
    url = candidate_pdp_url(candidate)
    if not url:
        return WalmartPdpCashProof(False, False, False, None, failure_reason="No exact Walmart product URL/ID was available for PDP fallback.")

    try:
        if callable(pdp_fetcher):
            html = await asyncio.wait_for(_call_pdp_fetcher(pdp_fetcher, url), timeout=8)
            return walmart_pdp_cash_proof_from_html(html, current_price=candidate.current_price, url=url)
        return await asyncio.wait_for(
            asyncio.to_thread(check_walmart_pdp_cash_truth, url, current_price=candidate.current_price),
            timeout=10,
        )
    except asyncio.TimeoutError:
        return WalmartPdpCashProof(True, False, False, None, url=url, failure_reason="Timed out checking exact Walmart PDP fallback.")
    except Exception as exc:
        return WalmartPdpCashProof(True, False, False, None, url=url, failure_reason=f"PDP fallback unavailable: {type(exc).__name__}")


async def _call_pdp_fetcher(fetcher: Any, url: str) -> str:
    result = fetcher(url)
    if inspect.isawaitable(result):
        result = await result
    return str(result or "")


async def _call_detail_method(detail_method: Any, product_id: str) -> Any:
    result = detail_method(product_id)
    if inspect.isawaitable(result):
        return await result
    return result


def _apply_cash_truth(candidate: SourceCandidate, truth: Any, *, source: str, detail_url: str = "") -> None:
    candidate.variant_attributes.update(truth.as_attributes())
    candidate.variant_attributes["cashAmountConfirmed"] = "yes"
    candidate.variant_attributes["cashProofSource"] = source
    if detail_url:
        candidate.variant_attributes["cashDetailUrl"] = detail_url
    signal = truth.signal()
    if signal not in candidate.signals:
        candidate.signals.append(signal)


def _scan_with_cash(scan: WalmartPromoScan | None, truth: Any) -> WalmartPromoScan:
    if scan is None:
        return WalmartPromoScan(cash=truth, raw_promo_paths=(truth.proof_path,))
    paths = tuple(scan.raw_promo_paths or ())
    if truth.proof_path not in paths:
        paths = (*paths, truth.proof_path)
    return WalmartPromoScan(
        cash=truth,
        cart_promo=scan.cart_promo,
        onepay=scan.onepay,
        markdown=scan.markdown,
        clearance=scan.clearance,
        generic=scan.generic,
        raw_promo_paths=paths,
    )


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
    badge = detect_walmart_cash_badge(candidate)
    cloned = replace(candidate, variant_attributes=dict(candidate.variant_attributes), signals=list(candidate.signals))
    for key in list(cloned.variant_attributes.keys()):
        if key.lower().startswith("walmartcash"):
            cloned.variant_attributes.pop(key, None)
    cloned.signals = [signal for signal in cloned.signals if "walmart cash" not in str(signal).lower()]

    if badge is not None:
        cloned.variant_attributes["cashBadgeSeen"] = "yes"
        cloned.variant_attributes["cashBadgeProofPath"] = badge.proof_path
        cloned.variant_attributes["cashBadgeProofText"] = badge.proof_text
        cloned.variant_attributes["cashBadgeRawValue"] = badge.raw_value
        cloned.variant_attributes["cashAmountConfirmed"] = "no"
        signal = "Walmart Cash badge seen; amount requires product detail/PDP proof"
        if signal not in cloned.signals:
            cloned.signals.append(signal)

    return cloned


def _prioritize_badge_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    return sorted(candidates, key=lambda candidate: 0 if _candidate_has_badge(candidate) else 1)


def _candidate_has_badge(candidate: SourceCandidate) -> bool:
    return str((candidate.variant_attributes or {}).get("cashBadgeSeen") or "").lower() == "yes"


def _has_non_cash_promo(scan: WalmartPromoScan | None) -> bool:
    if scan is None:
        return False
    return bool(scan.cart_promo or scan.onepay or scan.markdown or scan.clearance or scan.generic)


def _dedupe_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    seen: set[str] = set()
    output: list[SourceCandidate] = []

    for candidate in candidates:
        key = str(candidate.product_id or candidate.sku or candidate.product_url or candidate.title).strip().lower()
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
    product_id = str(candidate.product_id or candidate.sku or candidate.selected_offer_id or "n/a")
    badge_prefix = "Cash badge seen; " if result.cash_badge_seen else ""

    bits: list[str] = []
    if result.cash_amount_confirmed:
        amount = attrs.get("walmartCashAmount") or attrs.get("walmartCashSavings") or "?"
        source = attrs.get("cashProofSource") or "affiliate_detail"
        path = attrs.get("walmartCashProofPath") or "unknown proof path"
        bits.append(f"Walmart Cash amount confirmed ${amount} via {source} at {path}")
    elif result.cash_badge_seen:
        bits.append("badge candidate only; no confirmed Walmart Cash amount")

    if result.detail_checked:
        bits.append("affiliate detail checked")
    elif result.detail_attempted:
        bits.append(f"affiliate detail not checked — {result.note}")

    if result.pdp_attempted:
        if result.pdp_checked:
            if result.pdp_wording_seen:
                bits.append(f"PDP checked at {result.cash_detail_url}; Walmart Cash wording seen")
            else:
                bits.append(f"PDP checked at {result.cash_detail_url}; no Walmart Cash wording")
        else:
            bits.append(f"PDP not checked at {result.cash_detail_url or 'n/a'} — {result.cash_failure_reason}")

    if not bits:
        if not result.detail_checked:
            bits.append(f"detail proof not checked — {result.note}")
        else:
            bits.append("detail checked, no promo proof")

    scan = result.promo_scan
    if scan is not None:
        if scan.cart_promo:
            bits.append(f"Cart Promo at {scan.cart_promo.proof_path}")
        if scan.onepay:
            bits.append(f"OnePay at {scan.onepay.proof_path}")
        if scan.markdown:
            bits.append(f"Markdown at {scan.markdown.proof_path}")
        if scan.clearance:
            bits.append(f"Clearance at {scan.clearance.proof_path}")
        if scan.generic:
            bits.append(f"Generic promo at {scan.generic.proof_path}")

    if result.cash_failure_reason and result.cash_failure_reason not in "; ".join(bits):
        bits.append(result.cash_failure_reason)

    return f"{title} ({product_id}): {badge_prefix}" + "; ".join(bits)


def _badge_evidence_from_text(path: str, value: Any) -> WalmartCashBadgeEvidence | None:
    if _reject_badge_path(path):
        return None

    text = f"{path} {value}"
    lowered = str(text or "").lower()
    normalized = _norm(text)

    if any(term in lowered for term in ("onepay", "one pay", "cashback", "cash back", "cashrewards", "cash rewards", "credit card")):
        return None

    explicit_cash = "walmart cash" in lowered or "walmartcash" in normalized
    cash_reward = "cash reward" in lowered or "cashreward" in normalized
    reward_available = "reward available" in lowered or "rewardavailable" in normalized
    if not (explicit_cash or cash_reward or reward_available):
        return None

    # Badge/eligibility text is a candidate only. Amount confirmation still comes
    # from detail/PDP via product-level proof.
    proof_text = _clean_preview(value, 180)
    if not proof_text:
        proof_text = _clean_preview(path, 180)
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


def _choose_badge(current: WalmartCashBadgeEvidence | None, new: WalmartCashBadgeEvidence) -> WalmartCashBadgeEvidence:
    if current is None:
        return new
    current_rank = _badge_rank(current)
    new_rank = _badge_rank(new)
    return new if new_rank > current_rank else current


def _badge_rank(evidence: WalmartCashBadgeEvidence) -> tuple[int, int]:
    path = evidence.proof_path.lower()
    text = evidence.proof_text.lower()
    exact = 1 if "walmart cash" in text or "walmartcash" in _norm(text) or "walmartcash" in _norm(path) else 0
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


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean_preview(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
