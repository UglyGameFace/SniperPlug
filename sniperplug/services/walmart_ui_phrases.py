from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re


@dataclass(frozen=True)
class WalmartUiPhraseMatch:
    kind: str
    phrase: str
    reason: str


WALMART_CASH_AMOUNT_RE = re.compile(
    r"\b(?:get|earn|receive)\s+\$?\s*\d+(?:\.\d{1,2})?\s+(?:in\s+)?walmart\s+cash\b",
    re.IGNORECASE,
)
WALMART_CASH_AVAILABLE_RE = re.compile(r"\bwalmart\s+cash\s+available\b", re.IGNORECASE)
WALMART_CASH_MANUFACTURER_RE = re.compile(r"\bmanufacturer\s+offer\b", re.IGNORECASE)

WALMART_MARKDOWN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brollback\b", re.IGNORECASE),
    re.compile(r"\breduced\s+price\b", re.IGNORECASE),
    re.compile(r"\byou\s+save\s+\$?\s*\d", re.IGNORECASE),
    re.compile(r"\bnow\s+\$?\s*\d", re.IGNORECASE),
    re.compile(r"\bprice\s+when\s+purchased\s+online\b", re.IGNORECASE),
    re.compile(r"\bonline\s+price\b", re.IGNORECASE),
    re.compile(r"\bclearance\b", re.IGNORECASE),
)

WALMART_POPULARITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{2,}\+\s+bought\s+since\s+yesterday\b", re.IGNORECASE),
    re.compile(r"\bin\s+\d{2,}\+\s+people'?s\s+carts\b", re.IGNORECASE),
    re.compile(r"\bbest\s+seller\b", re.IGNORECASE),
    re.compile(r"\bpopular\s+pick\b", re.IGNORECASE),
)

WALMART_CONDITION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brestored\s*:\s*(?:premium|like\s+new|good|fair)\b", re.IGNORECASE),
    re.compile(r"\brestored\b", re.IGNORECASE),
    re.compile(r"\brefurbished\b", re.IGNORECASE),
    re.compile(r"\bopen[\s-]?box\b", re.IGNORECASE),
    re.compile(r"\blike[\s-]?new\b", re.IGNORECASE),
)


def normalize_ui_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def find_walmart_cash_ui_offer(value: Any) -> WalmartUiPhraseMatch | None:
    """Detect exact Walmart Cash UI copy, not generic safety/disclaimer text."""

    text = normalize_ui_text(value)
    if not text:
        return None

    amount_match = WALMART_CASH_AMOUNT_RE.search(text)
    if amount_match:
        return WalmartUiPhraseMatch("walmart_cash", amount_match.group(0), "Exact Walmart UI wording with dollar amount.")

    available_match = WALMART_CASH_AVAILABLE_RE.search(text)
    if available_match:
        return WalmartUiPhraseMatch("walmart_cash_badge", available_match.group(0), "Walmart Cash available badge wording.")

    lowered = text.lower()
    if "walmart cash" in lowered and WALMART_CASH_MANUFACTURER_RE.search(text):
        return WalmartUiPhraseMatch("walmart_cash_badge", "Walmart Cash + Manufacturer offer", "Walmart UI manufacturer-offer Cash badge wording.")

    return None


def has_walmart_cash_ui_offer(value: Any) -> bool:
    return find_walmart_cash_ui_offer(value) is not None


def find_walmart_markdown_ui_signal(value: Any) -> WalmartUiPhraseMatch | None:
    text = normalize_ui_text(value)
    for pattern in WALMART_MARKDOWN_PATTERNS:
        match = pattern.search(text)
        if match:
            return WalmartUiPhraseMatch("markdown_ui", match.group(0), "Walmart markdown UI wording.")
    return None


def has_walmart_markdown_ui_signal(value: Any) -> bool:
    return find_walmart_markdown_ui_signal(value) is not None


def find_walmart_popularity_ui_signal(value: Any) -> WalmartUiPhraseMatch | None:
    text = normalize_ui_text(value)
    for pattern in WALMART_POPULARITY_PATTERNS:
        match = pattern.search(text)
        if match:
            return WalmartUiPhraseMatch("popularity_ui", match.group(0), "Walmart popularity UI wording; not deal proof.")
    return None


def has_walmart_popularity_ui_signal(value: Any) -> bool:
    return find_walmart_popularity_ui_signal(value) is not None


def find_walmart_condition_ui_signal(value: Any) -> WalmartUiPhraseMatch | None:
    text = normalize_ui_text(value)
    for pattern in WALMART_CONDITION_PATTERNS:
        match = pattern.search(text)
        if match:
            return WalmartUiPhraseMatch("condition_ui", match.group(0), "Walmart condition/restored UI wording.")
    return None


def has_walmart_condition_ui_signal(value: Any) -> bool:
    return find_walmart_condition_ui_signal(value) is not None
