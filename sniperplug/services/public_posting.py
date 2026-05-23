from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_PUBLIC_RETAILERS = {"walmart", "home_depot", "bestbuy", "amazon"}


@dataclass(frozen=True)
class PublicPostingSettings:
    enabled: bool
    retailers: tuple[str, ...]

    def allows(self, retailer: str) -> bool:
        return self.enabled and normalize_retailer_key(retailer) in set(self.retailers)


def normalize_retailer_key(value: str | None) -> str:
    text = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "home": "home_depot",
        "homedepot": "home_depot",
        "home_depot": "home_depot",
        "hd": "home_depot",
        "walmart": "walmart",
        "wal_mart": "walmart",
        "best_buy": "bestbuy",
        "bestbuy": "bestbuy",
        "bb": "bestbuy",
        "amazon": "amazon",
        "amz": "amazon",
    }
    return aliases.get(text, text)


def parse_retailer_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    retailers: list[str] = []
    for piece in value.replace(";", ",").split(","):
        key = normalize_retailer_key(piece)
        if key and key in SUPPORTED_PUBLIC_RETAILERS and key not in retailers:
            retailers.append(key)
    return tuple(retailers)


def format_retailers(retailers: tuple[str, ...]) -> str:
    return ", ".join(f"`{retailer}`" for retailer in retailers) if retailers else "none"
