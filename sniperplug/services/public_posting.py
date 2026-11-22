from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Discord channel obfuscation (live for all bots 2026-11-16): channels the
# bot cannot View Channel on arrive over the Gateway with their name replaced
# by this sentinel and the CHANNEL_OBFUSCATED flag set, and are omitted
# entirely from HTTP channel lists. Name-based channel discovery must skip
# them explicitly instead of silently treating them as ordinary channels.
OBFUSCATED_CHANNEL_NAME = "___hidden___"
CHANNEL_OBFUSCATED_FLAG = 1 << 17


def channel_is_obfuscated(channel: Any) -> bool:
    """True when Discord is hiding this channel's real metadata from the bot.

    Defensive by design: plain fakes/objects without a name or flags simply
    report False, so existing callers and tests are unaffected.
    """

    if channel is None:
        return False
    if str(getattr(channel, "name", "") or "") == OBFUSCATED_CHANNEL_NAME:
        return True
    flags = getattr(channel, "flags", None)
    value = getattr(flags, "value", flags)
    try:
        return bool(int(value) & CHANNEL_OBFUSCATED_FLAG)
    except (TypeError, ValueError):
        return False


SUPPORTED_PUBLIC_RETAILERS = {
    "walmart",
    "home_depot",
    "bestbuy",
    "amazon",
    "hp",
    "target",
}
SUPPORTED_RETAILERS = SUPPORTED_PUBLIC_RETAILERS
CREDITED_RETAILERS = {"home_depot", "amazon"}


@dataclass(frozen=True)
class PublicPostingSettings:
    enabled: bool
    retailers: tuple[str, ...]

    def allows(self, retailer: str) -> bool:
        return self.enabled and normalize_retailer_key(retailer) in set(self.retailers)


@dataclass(frozen=True)
class RetailerAutoScanSettings:
    retailer: str
    enabled: bool

    @property
    def uses_limited_credits(self) -> bool:
        return self.retailer in CREDITED_RETAILERS


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
        "hp": "hp",
        "hp_store": "hp",
        "hp.com": "hp",
        "hewlett_packard": "hp",
        "target": "target",
        "target_store": "target",
        "target.com": "target",
    }
    return aliases.get(text, text)


def parse_retailer_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    retailers: list[str] = []
    for piece in value.replace(";", ",").split(","):
        key = normalize_retailer_key(piece)
        if key and key in SUPPORTED_RETAILERS and key not in retailers:
            retailers.append(key)
    return tuple(retailers)


def format_retailers(retailers: tuple[str, ...]) -> str:
    return ", ".join(f"`{retailer}`" for retailer in retailers) if retailers else "none"


def retailer_credit_note(retailer: str) -> str:
    key = normalize_retailer_key(retailer)
    if key in CREDITED_RETAILERS:
        return "Limited/paid quota risk: keep auto scans off unless you intentionally want SniperPlug spending credits."
    if key == "hp":
        return "HP Store coverage comes from the standalone first-party watcher and does not spend third-party API credits."
    if key == "target":
        return "Target coverage comes from the standalone sitemap + RedSky watcher and does not spend third-party API credits."
    return "No third-party credit warning registered for this store yet."
