from __future__ import annotations

"""Category-agnostic listing-contradiction detector.

A listing error of this kind is not a price error: the price itself is
ordinary for *some* quantity of the product, but the listing disagrees with
itself about which quantity that is. The title claims one amount, the
description (or the selected variant) claims a materially different amount,
and the price only makes sense for one of them.

This module compares *comparable quantity claims* extracted from the title,
the description text and the variant attributes:

- count claims (pack / pk / count / ct / pieces / pcs and generic countable
  unit nouns such as rolls or capsules) — compared like-for-like per unit;
- pairs claims — normalized to individual items (1 pair = 2 items) so they
  can be compared against plain count claims, and compared directly
  pair-for-pair;
- weight claims (oz / lb / g / kg) — converted to grams, weight only;
- volume claims (fl oz / ml / l / gal) — converted to millilitres,
  volume only.

Claims are never compared across dimensions, and a text that carries two
different values for the same unit is ambiguous and yields no claim for
that unit (fail closed — e.g. a description that also mentions a refill or
accessory pack in another size must not create a flag).

A contradiction alone is only a review note. A deliverable speculative lead
additionally requires value evidence from data the candidate already
carries: a reference/list/context price that is consistent with the larger
claim being worth far more than the asking price, or an asking price that
matches what the smaller claim would cost at the reference's per-unit rate
(the price is ordinary for the smaller amount — exactly the listing-error
shape). No external service is consulted.
"""

import re
from dataclasses import dataclass
from typing import Any, Iterable

# A claim must differ by at least this factor to count as a contradiction;
# smaller gaps are treated as rounding / format noise.
MATERIAL_RATIO = 1.5

_GRAMS = {"oz": 28.349523125, "ounce": 28.349523125, "ounces": 28.349523125,
          "lb": 453.59237, "lbs": 453.59237, "pound": 453.59237, "pounds": 453.59237,
          "g": 1.0, "gram": 1.0, "grams": 1.0,
          "kg": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0}
_MILLILITRES = {"fl oz": 29.5735295625, "floz": 29.5735295625,
                "fluid ounce": 29.5735295625, "fluid ounces": 29.5735295625,
                "ml": 1.0, "milliliter": 1.0, "milliliters": 1.0,
                "millilitre": 1.0, "millilitres": 1.0,
                "l": 1000.0, "liter": 1000.0, "liters": 1000.0,
                "litre": 1000.0, "litres": 1000.0,
                "gal": 3785.411784, "gallon": 3785.411784, "gallons": 3785.411784}
# Generic countable unit nouns. These are units, not products: the same
# nouns serve any category that ships in that unit.
_COUNT_NOUNS = {
    "roll", "capsule", "tablet", "bar", "wipe", "sheet", "pod", "can",
    "bottle", "diaper", "battery", "serving", "load", "sock", "stick",
    "bag", "box", "brick", "pouch", "tube", "jar", "packet", "sachet",
    "strip", "patch", "filter",
}

_NUM = r"(\d+(?:\.\d+)?)"
_VOLUME_RE = re.compile(
    _NUM + r"\s*(fl\.?\s*oz|fluid\s+ounces?|milliliters?|millilitres?|gallons?|gal|ml|liters?|litres?|l)\b",
    re.IGNORECASE,
)
_WEIGHT_RE = re.compile(
    _NUM + r"\s*(ounces?|oz|lbs?|pounds?|kilograms?|kg|grams?|g)\b",
    re.IGNORECASE,
)
_PAIRS_RE = re.compile(_NUM + r"\s*[- ]?\s*pairs?\b", re.IGNORECASE)
_ITEMS_RE = re.compile(
    _NUM + r"\s*[- ]?\s*(packs?|pk|count|ct|pieces?|pcs)\b",
    re.IGNORECASE,
)
_OF_RE = re.compile(
    r"\b(?:pack|packs|package|box|case|set|bag|canister|container)\s+of\s+" + _NUM + r"\b",
    re.IGNORECASE,
)
_NOUN_RE = re.compile(
    _NUM + r"\s*[- ]?\s*(" + "|".join(sorted(_COUNT_NOUNS)) + r")s?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuantityClaim:
    dimension: str  # "count" | "weight" | "volume"
    key: str  # like-for-like comparison key within the dimension
    value: float  # normalized: items / grams / millilitres
    raw: str  # the claim as written, for display
    unit: str


@dataclass(frozen=True)
class ListingContradiction:
    title_claim: QuantityClaim
    other_claim: QuantityClaim
    other_source: str  # "description" | "variant"
    ratio: float
    is_lead: bool
    value_evidence: str | None

    @property
    def larger_value(self) -> float:
        return max(self.title_claim.value, self.other_claim.value)

    @property
    def smaller_value(self) -> float:
        return min(self.title_claim.value, self.other_claim.value)

    def note(self) -> str:
        return (
            f"Listing contradiction: title says {self.title_claim.raw}, "
            f"{self.other_source} says {self.other_claim.raw} — "
            "you may receive the smaller amount."
        )


def _norm_unit(unit: str) -> str:
    return " ".join(unit.lower().replace(".", " ").split())


def _claims_from_text(text: str | None) -> dict[tuple[str, str], QuantityClaim | None]:
    """Extract claims keyed by (dimension, key); a key seen with two
    different normalized values maps to None (ambiguous, fail closed)."""
    found: dict[tuple[str, str], QuantityClaim | None] = {}
    if not text:
        return found
    claimed_spans: list[tuple[int, int]] = []

    def overlaps(span: tuple[int, int]) -> bool:
        return any(span[0] < end and start < span[1] for start, end in claimed_spans)

    def add(claim: QuantityClaim, span: tuple[int, int]) -> None:
        claimed_spans.append(span)
        key = (claim.dimension, claim.key)
        existing = found.get(key, claim)
        if existing is not None and abs(existing.value - claim.value) > 0.01:
            found[key] = None  # ambiguous: two values for the same unit
        else:
            found[key] = existing if existing is not None else claim

    for match in _VOLUME_RE.finditer(text):
        unit = _norm_unit(match.group(2))
        factor = _MILLILITRES.get(unit)
        if factor is None:
            continue
        add(QuantityClaim("volume", "volume", float(match.group(1)) * factor,
                          match.group(0).strip(), unit), match.span())
    for match in _WEIGHT_RE.finditer(text):
        if overlaps(match.span()):
            continue  # already claimed as volume (fl oz)
        unit = _norm_unit(match.group(2))
        factor = _GRAMS.get(unit)
        if factor is None:
            continue
        add(QuantityClaim("weight", "weight", float(match.group(1)) * factor,
                          match.group(0).strip(), unit), match.span())
    for match in _PAIRS_RE.finditer(text):
        if overlaps(match.span()):
            continue
        count = float(match.group(1))
        add(QuantityClaim("count", "items", count * 2,
                          match.group(0).strip(), "pairs"), match.span())
    for match in _ITEMS_RE.finditer(text):
        if overlaps(match.span()):
            continue
        add(QuantityClaim("count", "items", float(match.group(1)),
                          match.group(0).strip(), _norm_unit(match.group(2))), match.span())
    for match in _OF_RE.finditer(text):
        if overlaps(match.span()):
            continue
        add(QuantityClaim("count", "items", float(match.group(1)),
                          match.group(0).strip(), "of-count"), match.span())
    for match in _NOUN_RE.finditer(text):
        if overlaps(match.span()):
            continue
        noun = _norm_unit(match.group(2))
        add(QuantityClaim("count", noun, float(match.group(1)),
                          match.group(0).strip(), noun), match.span())
    return found


def extract_claims(text: str | None) -> dict[tuple[str, str], QuantityClaim]:
    return {k: v for k, v in _claims_from_text(text).items() if v is not None}


def _positive(value: Any) -> float | None:
    try:
        number = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return number if number > 0 else None


def _reference_prices(candidate: Any) -> list[float]:
    prices: list[float] = []
    for value in (
        getattr(candidate, "typical_price", None),
        getattr(candidate, "api_reference_price", None),
    ):
        parsed = _positive(value)
        if parsed is not None:
            prices.append(parsed)
    attrs = getattr(candidate, "variant_attributes", None) or {}
    if isinstance(attrs, dict):
        # marketplaceCompPrice is the existing cross-seller comp the Walmart
        # marketplace-comp machinery already stores on the candidate: the
        # best price for the same item from another seller/vendor. It is
        # value evidence of exactly the cross-vendor kind, when present.
        for key in ("referenceContextPrice", "wasPrice", "listPrice", "msrp", "marketplaceCompPrice"):
            parsed = _positive(attrs.get(key))
            if parsed is not None:
                prices.append(parsed)
    return prices


def _value_evidence(
    current: float | None, references: Iterable[float], *, larger: float, smaller: float
) -> str | None:
    if current is None or larger <= smaller or smaller <= 0:
        return None
    for reference in references:
        if reference >= MATERIAL_RATIO * current:
            return (
                f"reference price ${reference:,.2f} is consistent with the "
                f"larger claim being worth far more than the ${current:,.2f} asking price"
            )
        predicted_smaller = reference * (smaller / larger)
        if predicted_smaller > 0 and abs(current - predicted_smaller) <= 0.35 * predicted_smaller:
            return (
                f"asking price ${current:,.2f} matches the smaller claim's "
                f"share (${predicted_smaller:,.2f}) of the ${reference:,.2f} reference price"
            )
    return None


def detect_listing_contradiction(
    *,
    title: str | None,
    description: str | None = None,
    variant_texts: Iterable[str | None] = (),
    candidate: Any = None,
    current_price: float | None = None,
    reference_prices: Iterable[float] = (),
) -> ListingContradiction | None:
    """Return the strongest like-for-like contradiction, or None.

    Title claims are compared against description claims first, then
    against variant claims. When ``candidate`` is given, its price fields
    supply the value evidence; otherwise the explicit price arguments do.
    """
    title_claims = extract_claims(title)
    if not title_claims:
        return None
    other_sources: list[tuple[str, dict[tuple[str, str], QuantityClaim]]] = []
    description_claims = extract_claims(description)
    if description_claims:
        other_sources.append(("description", description_claims))
    variant_claims: dict[tuple[str, str], QuantityClaim] = {}
    for text in variant_texts:
        for key, claim in extract_claims(text).items():
            existing = variant_claims.get(key)
            if existing is not None and abs(existing.value - claim.value) > 0.01:
                variant_claims.pop(key, None)  # ambiguous variant text
            else:
                variant_claims[key] = claim
    if variant_claims:
        other_sources.append(("variant", variant_claims))

    best: ListingContradiction | None = None
    for source, claims in other_sources:
        pairs: list[tuple[QuantityClaim, QuantityClaim]] = []
        for key, title_claim in title_claims.items():
            other = claims.get(key)
            if other is not None:
                pairs.append((title_claim, other))
        if not pairs:
            # Generic-count fallback, count dimension only: a title that
            # claims a plain "90 count" and a description whose only count
            # claim is a specific unit noun ("60 sheets") are describing
            # the same product quantity in different words. Only when the
            # other text carries exactly one count claim — otherwise the
            # intended unit is ambiguous and we fail closed.
            title_items = title_claims.get(("count", "items"))
            other_counts = [c for (dim, _), c in claims.items() if dim == "count"]
            title_counts = [c for (dim, _), c in title_claims.items() if dim == "count"]
            if title_items is not None and len(other_counts) == 1 and other_counts[0].key != "items":
                pairs.append((title_items, other_counts[0]))
            elif len(title_counts) == 1 and title_counts[0].key != "items":
                other_items = claims.get(("count", "items"))
                if other_items is not None:
                    pairs.append((title_counts[0], other_items))
        for title_claim, other in pairs:
            larger = max(title_claim.value, other.value)
            smaller = min(title_claim.value, other.value)
            if smaller <= 0:
                continue
            ratio = larger / smaller
            if ratio < MATERIAL_RATIO:
                continue
            if best is not None and ratio <= best.ratio:
                continue
            if candidate is not None:
                evidence = _value_evidence(
                    _positive(getattr(candidate, "current_price", None)),
                    _reference_prices(candidate),
                    larger=larger, smaller=smaller,
                )
            else:
                evidence = _value_evidence(
                    current_price, reference_prices, larger=larger, smaller=smaller
                )
            best = ListingContradiction(
                title_claim=title_claim,
                other_claim=other,
                other_source=source,
                ratio=ratio,
                is_lead=evidence is not None,
                value_evidence=evidence,
            )
    return best


def candidate_contradiction(candidate: Any) -> ListingContradiction | None:
    """Detect a contradiction on a SourceCandidate-like object."""
    attrs = getattr(candidate, "variant_attributes", None) or {}
    variant_texts: list[str | None] = []
    if isinstance(attrs, dict):
        for key in ("packSize", "size"):
            value = attrs.get(key)
            if value:
                variant_texts.append(str(value))
    pack_size = getattr(candidate, "pack_size", None)
    if pack_size:
        variant_texts.append(str(pack_size))
    variant_label = getattr(candidate, "variant_label", None)
    if variant_label:
        variant_texts.append(str(variant_label))
    return detect_listing_contradiction(
        title=getattr(candidate, "title", None),
        description=getattr(candidate, "description", None),
        variant_texts=variant_texts,
        candidate=candidate,
    )


# ---------------------------------------------------------------------------
# Condition grades
#
# Grades are recognized from structured offer data (the condition field /
# variant attributes — the value there IS a grade) and, more conservatively,
# from title/description wording. Ambiguous wording yields no grade: the
# condition is unproven and the lead simply carries no condition label
# rather than a guessed one. Seller-defined wording maps only where it
# maps cleanly ("renewed"/"restored" -> Refurbished, "pre-owned" -> Used).
# ---------------------------------------------------------------------------

CONDITION_GRADES = (
    "New", "Open Box", "Like New", "Very Good", "Good", "Acceptable",
    "Used", "Refurbished",
)

_GRADE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Like New", re.compile(r"like\s+new", re.IGNORECASE)),
    ("Open Box", re.compile(r"open[\s-]?box", re.IGNORECASE)),
    ("Very Good", re.compile(r"very\s+good", re.IGNORECASE)),
    ("Refurbished", re.compile(r"certified\s+refurbished|refurbished|renewed|restored", re.IGNORECASE)),
    ("Used", re.compile(r"pre[\s-]?owned", re.IGNORECASE)),
    ("Acceptable", re.compile(r"\bacceptable\b", re.IGNORECASE)),
    ("Used", re.compile(r"\bused\b", re.IGNORECASE)),
    ("New", re.compile(r"brand\s+new", re.IGNORECASE)),
]


def grade_from_condition_value(value: Any) -> str | None:
    """Grade from a structured condition field, where bare grade words
    (``Good``, ``Acceptable``) are unambiguous because the field is a grade."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    used_grade = re.search(
        r"used\s*[-–]\s*(like\s+new|very\s+good|good|acceptable)", text, re.IGNORECASE
    )
    if used_grade:
        return " ".join(used_grade.group(1).split()).title()
    for grade, pattern in _GRADE_PATTERNS:
        if pattern.search(text):
            return grade
    lowered = text.lower()
    if lowered == "new":
        return "New"
    if lowered == "good":
        return "Good"
    return None


def grade_from_text(text: str | None, *, title: bool = False) -> str | None:
    """Grade from free text, conservatively. ``Good``/``New`` as bare words
    are ordinary prose in titles and descriptions, so they only count with
    condition context (a badge-style position or an explicit condition
    phrase)."""
    if not text:
        return None
    for grade, pattern in _GRADE_PATTERNS:
        if pattern.search(text):
            return grade
    lowered = text.lower()
    if re.search(r"condition\s*[:\-]?\s*(new|good)\b", lowered):
        return "New" if "new" in lowered.split("condition", 1)[1][:12] else "Good"
    if title and re.search(r"(^|[-–|(])\s*new\s*($|[-–|)])", lowered):
        return "New"
    if re.search(r"used\s*[-–]\s*good\b", lowered):
        return "Good"
    return None


@dataclass(frozen=True)
class ConditionContradiction:
    title_grade: str
    other_grade: str
    other_source: str  # "offer" | "description" | "variant"

    def note(self) -> str:
        return (
            f"Condition contradiction: title says {self.title_grade}, "
            f"{self.other_source} says {self.other_grade} — "
            "the item is not proven to be the titled condition."
        )


def candidate_condition(candidate: Any) -> str | None:
    """The offer's proven grade: structured condition data first, then
    variant attributes, then description/title wording (fail closed)."""
    for value in (
        getattr(candidate, "condition", None),
        getattr(candidate, "api_condition", None),
    ):
        grade = grade_from_condition_value(value)
        if grade is not None:
            return grade
    attrs = getattr(candidate, "variant_attributes", None) or {}
    if isinstance(attrs, dict):
        for key in ("condition", "offerCondition", "conditionGroup", "conditionLabel"):
            grade = grade_from_condition_value(attrs.get(key))
            if grade is not None:
                return grade
    return grade_from_text(getattr(candidate, "description", None)) or grade_from_text(
        getattr(candidate, "title", None), title=True
    )


def detect_condition_contradiction(candidate: Any) -> ConditionContradiction | None:
    title_grade = grade_from_text(getattr(candidate, "title", None), title=True)
    if title_grade is None:
        return None
    others: list[tuple[str, str | None]] = []
    structured = None
    for value in (
        getattr(candidate, "condition", None),
        getattr(candidate, "api_condition", None),
    ):
        structured = grade_from_condition_value(value)
        if structured is not None:
            break
    if structured is None:
        attrs = getattr(candidate, "variant_attributes", None) or {}
        if isinstance(attrs, dict):
            for key in ("condition", "offerCondition", "conditionGroup", "conditionLabel"):
                structured = grade_from_condition_value(attrs.get(key))
                if structured is not None:
                    break
    others.append(("offer", structured))
    others.append(("description", grade_from_text(getattr(candidate, "description", None))))
    for source, grade in others:
        if grade is not None and grade != title_grade:
            return ConditionContradiction(title_grade, grade, source)
    return None


def _price_only_value_evidence(candidate: Any) -> str | None:
    current = _positive(getattr(candidate, "current_price", None))
    if current is None:
        return None
    for reference in _reference_prices(candidate):
        if reference >= MATERIAL_RATIO * current:
            return (
                f"reference price ${reference:,.2f} is far above the "
                f"${current:,.2f} asking price"
            )
    return None


def build_listing_error_lead_card(candidate: Any) -> Any | None:
    """Build the speculative listing-error lead card, or None.

    Reuses the scout card builder exactly as the extreme-price lane does,
    then replaces its framing: this is a listing contradiction lead, NOT a
    verified markdown, and no discount percentage is claimed. The card is
    deliverable only when the detector found value evidence (is_lead).
    """
    contradiction = candidate_contradiction(candidate)
    condition_clash = detect_condition_contradiction(candidate)
    quantity_lead = contradiction is not None and contradiction.is_lead
    condition_evidence = _price_only_value_evidence(candidate) if condition_clash else None
    if not quantity_lead and not (condition_clash is not None and condition_evidence is not None):
        return None
    from sniperplug.services.low_price_scout import ScoutLead, build_scout_card

    evidence = (contradiction.value_evidence if quantity_lead else None) or condition_evidence or ""
    lead = ScoutLead(
        candidate=candidate,
        score=70,
        reasons=("listing contradiction lead", evidence),
    )
    card = build_scout_card(lead)
    card.url = getattr(candidate, "direct_product_url", None) or candidate.product_url
    card.embed.url = card.url
    card.embed.title = "Listing-error lead: " + str(candidate.title or "")[:155]
    # The scout card derives a % from any raw reference it finds; a listing
    # contradiction must never present that as a verified discount. Strip
    # every scout price field and replace it with the contradiction facts.
    card.embed.clear_fields()
    current = _positive(getattr(candidate, "current_price", None))
    price_text = f"Listed price: **${current:,.2f}**." if current else "Listed price not returned."
    facts: list[str] = [price_text]
    if quantity_lead and contradiction is not None:
        facts.append(contradiction.note())
    if condition_clash is not None:
        facts.append(condition_clash.note())
    facts.append(f"Value evidence: {evidence}.")
    grade = candidate_condition(candidate)
    if grade is not None:
        if grade == "New":
            facts.append("Condition: New (as carried by the offer data).")
        else:
            facts.append(f"Condition: {grade} — this is NOT a new item.")
            facts.append(
                "The reference price above is the listing's reference/list "
                "price (the new-item price where no same-condition comp is "
                "carried); the comparison is against that price, not a "
                "same-condition comp."
            )
    seller = str(getattr(candidate, "seller_name", None) or "").strip()
    if seller:
        facts.append(f"Seller: {seller}. Judge seller risk before buying.")
    facts.append(
        "No verified was-price or discount percentage is claimed. "
        "Recheck the exact variant, quantity, condition, seller and checkout price."
    )
    card.embed.add_field(
        name="Listing contradiction, NOT a verified markdown",
        value=" ".join(facts),
        inline=False,
    )
    card.embed.set_footer(
        text="Speculative listing-error lead; quantity/condition unverified; verify before buying"
    )
    card.listing_contradiction_candidate = candidate
    card.listing_error_lead = True
    card.should_alert = True
    return card
