from __future__ import annotations

from typing import Any


PREFLIGHT_REASON_ATTR = "autoscan_preflight_reason"


def _title(card: Any) -> str:
    label = str(getattr(card, "label", "") or "").strip()
    if label:
        return label
    embed = getattr(card, "embed", None)
    if embed is not None:
        title = str(getattr(embed, "title", "") or "").strip()
        if title:
            return title
    return "Untitled Walmart lead"


def _price(card: Any) -> str:
    value = getattr(card, "current_price", None)
    if value is None:
        return "price unknown"
    try:
        return f"${float(value):.2f}"
    except Exception:
        return str(value)


def _discount(card: Any) -> str:
    try:
        return f"{float(getattr(card, 'discount', 0) or 0):.0f}%"
    except Exception:
        return "0%"


def _score(card: Any) -> int:
    try:
        return int(float(getattr(card, "score", 0) or 0))
    except Exception:
        return 0


def _short(text: str, limit: int = 70) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"


def _ids(cards: list[Any]) -> set[int]:
    return {id(card) for card in cards}


def _preflight_reason(card: Any) -> str:
    return " ".join(
        str(getattr(card, PREFLIGHT_REASON_ATTR, "") or "").split()
    )


def explain_autoscan_decision_trail(
    *,
    all_verified_cards: list[Any],
    confidence_cards: list[Any],
    public_candidates: list[Any],
    fresh_cards: list[Any],
    min_discount: int,
    confidence_floor: int,
    limit: int = 8,
) -> str:
    """Explain why the top candidates did or did not reach public posting.

    The fresh-deal preflight annotates every processed public candidate with its
    concrete quality, duplicate, reservation, or cache decision. This report
    surfaces that exact reason rather than collapsing unrelated gates together.
    """
    if not all_verified_cards:
        return (
            "No verified markdown cards were produced. The scan may have found products, "
            "but none had trusted enough Walmart price/reference proof to become verified cards."
        )

    confidence_ids = _ids(confidence_cards)
    public_ids = _ids(public_candidates)
    fresh_ids = _ids(fresh_cards)

    lines: list[str] = []
    for index, card in enumerate(all_verified_cards[: max(1, int(limit))], start=1):
        reasons: list[str] = []
        discount_value = 0.0
        try:
            discount_value = float(getattr(card, "discount", 0) or 0)
        except Exception:
            discount_value = 0.0

        if discount_value < int(min_discount):
            reasons.append(f"below threshold {int(min_discount)}%")
        if id(card) not in confidence_ids:
            reasons.append(f"below confidence floor {int(confidence_floor)}/100")
        if id(card) not in public_ids:
            reasons.append("not in final public-quality lane")
        if id(card) in public_ids and id(card) not in fresh_ids:
            reasons.append(_preflight_reason(card) or "blocked by an unidentified preflight gate")
        if id(card) in fresh_ids:
            preflight = _preflight_reason(card)
            reasons.append(
                f"sent to public guard ({preflight})"
                if preflight
                else "sent to public guard"
            )

        if not reasons:
            reasons.append("blocked by later public/category guard")

        lines.append(
            f"#{index} **{_short(_title(card), 58)}** — {_price(card)} • "
            f"{_discount(card)} • score {_score(card)} → {', '.join(reasons)}"
        )

    return "\n".join(lines)[:1800]


def no_post_plain_english(
    *,
    verified_count: int,
    public_candidate_count: int,
    fresh_count: int,
    posted_count: int,
) -> str:
    if posted_count:
        return f"Posted **{posted_count}** public deal(s)."
    if verified_count <= 0:
        return "No verified markdown cards were created from this scan."
    if public_candidate_count <= 0:
        return "Verified cards existed, but none reached the final public-quality lane."
    if fresh_count <= 0:
        return "Public-quality cards existed, but fresh/duplicate/preflight gates blocked them; the detailed decision trail now shows the exact duplicate, reservation, quality, or cache reason for each one."
    return "Fresh public-quality cards reached the public guard, but final posting gates blocked them."
