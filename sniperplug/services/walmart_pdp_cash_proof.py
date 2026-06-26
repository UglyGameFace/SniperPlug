from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import html as html_lib
import json
import re
import urllib.parse
import urllib.request

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_cash_api_truth import WalmartCashApiTruth, extract_walmart_cash_api_truth


@dataclass(frozen=True)
class WalmartPdpCashProof:
    attempted: bool
    checked: bool
    wording_seen: bool
    cash_truth: WalmartCashApiTruth | None
    url: str = ""
    proof_path: str = ""
    failure_reason: str = ""


def candidate_pdp_url(candidate: SourceCandidate) -> str:
    """Return an exact Walmart PDP URL backed by the row identity, never by the user query."""

    for raw_url in (candidate.direct_product_url, candidate.product_url):
        url = _exact_walmart_pdp_url(raw_url)
        if url:
            return url

    for identity in (candidate.product_id, candidate.sku, candidate.selected_offer_id):
        clean = str(identity or "").strip()
        if clean:
            return f"https://www.walmart.com/ip/{urllib.parse.quote(clean, safe='')}"

    return ""


def check_walmart_pdp_cash_truth(
    url: str,
    *,
    current_price: float | None,
    fetcher: Callable[[str], str] | None = None,
    timeout: int = 8,
) -> WalmartPdpCashProof:
    """Fetch an exact Walmart PDP and parse private-only Walmart Cash proof.

    This is not a public posting proof path. It only confirms Walmart Cash when
    the exact product page contains Walmart Cash wording plus a sane dollar
    amount.
    """

    clean_url = _exact_walmart_pdp_url(url)
    if not clean_url:
        return WalmartPdpCashProof(False, False, False, None, url=str(url or ""), failure_reason="No exact Walmart product URL was available for PDP proof.")

    try:
        html = fetcher(clean_url) if fetcher is not None else fetch_public_walmart_pdp_html(clean_url, timeout=timeout)
    except Exception as exc:
        return WalmartPdpCashProof(True, False, False, None, url=clean_url, failure_reason=f"PDP fetch unavailable: {type(exc).__name__}")

    return walmart_pdp_cash_proof_from_html(html, current_price=current_price, url=clean_url)


def fetch_public_walmart_pdp_html(url: str, *, timeout: int = 8) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": "Mozilla/5.0 SniperPlug/1.0 WalmartCashProof",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8", errors="replace")


def walmart_pdp_cash_proof_from_html(html: str, *, current_price: float | None, url: str = "") -> WalmartPdpCashProof:
    text = str(html or "")
    wording_seen = _has_walmart_cash_wording(text)
    best: WalmartCashApiTruth | None = None

    for index, payload in enumerate(_script_json_payloads(text)):
        truth = extract_walmart_cash_api_truth({"walmartPdp": payload}, current_price=current_price)
        if truth is not None:
            proof = WalmartCashApiTruth(
                amount=truth.amount,
                proof_path=f"walmart_pdp.script_json[{index}].{truth.proof_path}",
                proof_label="Walmart PDP embedded JSON",
                proof_text=truth.proof_text,
                raw_value=truth.raw_value,
            )
            best = _choose_truth(best, proof)

    plain = _plain_text(text)
    for index, snippet in enumerate(_snippets_around_walmart_cash(plain)):
        proof = _truth_from_pdp_text(snippet, path=f"walmart_pdp.text[{index}]", current_price=current_price)
        if proof is not None:
            best = _choose_truth(best, proof)

    if best is not None:
        return WalmartPdpCashProof(True, True, True, best, url=url, proof_path=best.proof_path)

    if wording_seen:
        return WalmartPdpCashProof(True, True, True, None, url=url, failure_reason="Walmart Cash wording found, but no sane dollar amount was exposed on the exact PDP.")

    return WalmartPdpCashProof(True, True, False, None, url=url, failure_reason="Exact Walmart PDP checked; no Walmart Cash wording was exposed.")


def extract_walmart_cash_from_pdp_html(html: str, *, current_price: float | None = None, url: str = "") -> WalmartCashApiTruth | None:
    return walmart_pdp_cash_proof_from_html(html, current_price=current_price, url=url).cash_truth


def _truth_from_pdp_text(snippet: str, *, path: str, current_price: float | None) -> WalmartCashApiTruth | None:
    cleaned = _clean(snippet, 260)
    lowered = cleaned.lower()
    normalized = _norm(cleaned)

    if "walmart cash" not in lowered and "walmartcash" not in normalized:
        return None
    if any(term in lowered for term in ("onepay", "one pay", "credit card", "cashback", "cash back", "cashrewards", "cash rewards")):
        return None

    amount = _amount_near_walmart_cash(cleaned)
    if not _amount_is_sane(amount, current_price=current_price):
        return None

    return WalmartCashApiTruth(
        amount=float(amount),
        proof_path=path,
        proof_label="Walmart PDP text",
        proof_text=cleaned,
        raw_value=cleaned,
    )


def _script_json_payloads(html: str) -> list[Any]:
    payloads: list[Any] = []
    for body in re.findall(r"<script\b[^>]*>(.*?)</script>", str(html or ""), flags=re.IGNORECASE | re.DOTALL):
        body = html_lib.unescape(body).strip()
        if not body:
            continue
        parsed = _parse_jsonish_script(body)
        if parsed is not None:
            payloads.append(parsed)
    return payloads[:20]


def _parse_jsonish_script(body: str) -> Any | None:
    candidates = [body]

    assignment = re.search(r"=\s*({.*})\s*;?\s*$", body, flags=re.DOTALL)
    if assignment:
        candidates.append(assignment.group(1))

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate[0] not in "[{":
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _snippets_around_walmart_cash(text: str) -> list[str]:
    source = str(text or "")
    lowered = source.lower()
    positions = [match.start() for match in re.finditer(r"walmart\s+cash|walmartcash", lowered)]
    snippets: list[str] = []
    seen: set[str] = set()

    for position in positions[:20]:
        start = max(0, position - 140)
        end = min(len(source), position + 180)
        snippet = _clean(source[start:end], 320)
        key = snippet.lower()
        if snippet and key not in seen:
            seen.add(key)
            snippets.append(snippet)
    return snippets


def _amount_near_walmart_cash(text: str) -> float | None:
    raw = str(text or "")
    lowered = raw.lower()

    cash_match = re.search(r"walmart\s+cash|walmartcash", lowered)
    if not cash_match:
        return None
    cash_index = cash_match.start()

    money_matches = list(re.finditer(r"\$\s*(\d+(?:\.\d{1,2})?)", raw))
    nearby_money = [match for match in money_matches if abs(match.start() - cash_index) <= 90]
    if nearby_money:
        best = min(nearby_money, key=lambda match: abs(match.start() - cash_index))
        return _float_or_none(best.group(1))

    patterns = (
        r"(?:earn|get|receive|save)\s+(\d+(?:\.\d{1,2})?)\s+(?:in\s+)?walmart\s+cash",
        r"walmart\s+cash[^0-9$]{0,80}(\d+(?:\.\d{1,2})?)",
    )
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return _float_or_none(match.group(1))
    return None


def _exact_walmart_pdp_url(url: Any) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""

    parsed = urllib.parse.urlparse(raw)
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if "walmart.com" not in host or "/ip/" not in path:
        return ""

    return urllib.parse.urlunparse((parsed.scheme or "https", parsed.netloc, path, "", "", ""))


def _has_walmart_cash_wording(value: Any) -> bool:
    text = str(value or "").lower()
    return "walmart cash" in text or "walmartcash" in _norm(text)


def _plain_text(html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", str(html or ""), flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return html_lib.unescape(" ".join(text.split()))


def _amount_is_sane(amount: float | None, *, current_price: float | None) -> bool:
    if amount is None or amount <= 0:
        return False
    if amount >= 10_000:
        return False
    if current_price is None or current_price <= 0:
        return amount <= 200
    return amount <= max(float(current_price) * 1.10, float(current_price) + 5.00)


def _choose_truth(current: WalmartCashApiTruth | None, new: WalmartCashApiTruth) -> WalmartCashApiTruth:
    if current is None:
        return new
    if new.amount > current.amount:
        return new
    if len(new.proof_path) < len(current.proof_path):
        return new
    return current


def _float_or_none(value: Any) -> float | None:
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
