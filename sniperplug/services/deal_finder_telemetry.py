from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sniperplug.models.candidate import SourceCandidate


@dataclass(frozen=True)
class SearchRouteStats:
    query: str
    pages_checked: int = 0
    returned_products: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def score(self) -> int:
        warning_penalty = len(self.warnings) * 3
        return max(0, self.returned_products + self.pages_checked - warning_penalty)


def tag_candidates_with_route(candidates: Iterable[SourceCandidate], *, query: str) -> None:
    """Attach finder route provenance without changing the provider contract."""
    for candidate in candidates:
        attrs = candidate.variant_attributes
        attrs.setdefault("finderSourceQuery", query)
        if query not in attrs.get("finderSourceQueries", "").split(" | "):
            existing = attrs.get("finderSourceQueries")
            attrs["finderSourceQueries"] = f"{existing} | {query}" if existing else query


def merge_route_stats(stats: Iterable[SearchRouteStats]) -> tuple[SearchRouteStats, ...]:
    grouped: dict[str, dict[str, object]] = {}
    for stat in stats:
        bucket = grouped.setdefault(stat.query, {"pages": 0, "products": 0, "warnings": []})
        bucket["pages"] = int(bucket["pages"]) + stat.pages_checked
        bucket["products"] = int(bucket["products"]) + stat.returned_products
        warnings = bucket["warnings"]
        assert isinstance(warnings, list)
        for warning in stat.warnings:
            if warning not in warnings:
                warnings.append(warning)
    merged = [
        SearchRouteStats(
            query=query,
            pages_checked=int(data["pages"]),
            returned_products=int(data["products"]),
            warnings=tuple(data["warnings"]),
        )
        for query, data in grouped.items()
    ]
    return tuple(sorted(merged, key=lambda item: item.score, reverse=True))


def top_route_lines(stats: Iterable[SearchRouteStats], *, limit: int = 5) -> list[str]:
    lines: list[str] = []
    for stat in list(stats)[:limit]:
        lines.append(
            f"• `{stat.query}` — **{stat.returned_products}** products across **{stat.pages_checked}** page(s)"
            + (f" • {len(stat.warnings)} warning(s)" if stat.warnings else "")
        )
    return lines
