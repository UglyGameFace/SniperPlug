"""Single-source browser identity for the direct Home Depot lane.

The federation gateway serves real data only when the TLS fingerprint,
the User-Agent and the client-hint headers all describe the SAME Chrome
major version (measured: any mismatch returns HTTP 206 "Generic Errors
API"). Every identity value used by the direct lane is derived here from
one Chrome major per profile so the three can never drift apart.

The default profile tracks the Chrome target pinned by the installed
``curl_cffi`` build (its ``DEFAULT_CHROME``), because the impersonation
target and the declared version must be the browser the TLS stack is
actually mimicking. ``SNIPERPLUG_HD_CHROME_MAJOR`` can pin a different
major when a newer profile is known to be accepted.

No cookies are ever part of this identity: the gateway needs none, and
flagged Akamai cookies were measured to poison cookieless access.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Profiles tried, in order, when the working profile starts returning
# 206. The default (curl_cffi's current target) always leads; the rest
# are recent Chrome majors measured against the gateway in Nov 2026.
_SWEEP_MAJORS: tuple[int, ...] = (146, 142, 150, 151, 131)

# Per-operation routing-header attempts, in order. Header values drift
# over time (searchModel has been captured with both "search" and
# "general-merchandise"), so each op carries its known-good attempts and
# the client walks them before declaring a call blocked.
OP_HEADER_ATTEMPTS: dict[str, tuple[tuple[str, str], ...]] = {
    "searchModel": (
        ("search", "search"),
        ("general-merchandise", "general-merchandise"),
    ),
    "productClientOnlyProduct": (
        ("general-merchandise", "thd-storefront"),
        ("general-merchandise", "general-merchandise"),
    ),
    "mediaPriceInventory": (
        ("general-merchandise", "thd-storefront"),
        ("general-merchandise", "general-merchandise"),
    ),
}

GATEWAY_HOSTS: tuple[str, ...] = (
    "apionline.homedepot.com",
    "www.homedepot.com",
)

_FALLBACK_MAJOR = 152


def default_chrome_major() -> int:
    """Chrome major for the default profile.

    Order: explicit env pin, then the installed curl_cffi build's own
    current Chrome target, then a static fallback.
    """
    raw = (os.getenv("SNIPERPLUG_HD_CHROME_MAJOR") or "").strip()
    if raw.isdigit():
        return int(raw)
    try:
        from curl_cffi.requests.impersonate import DEFAULT_CHROME

        match = re.search(r"(\d+)", str(DEFAULT_CHROME))
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return _FALLBACK_MAJOR


def sweep_majors() -> tuple[int, ...]:
    """Ordered unique Chrome majors the client may present."""
    ordered: list[int] = []
    for major in (default_chrome_major(), *_SWEEP_MAJORS):
        if major not in ordered:
            ordered.append(major)
    return tuple(ordered)


@dataclass(frozen=True)
class HdIdentity:
    """One consistent browser identity: TLS target + UA + hints."""

    major: int

    @property
    def impersonate(self) -> str:
        return f"chrome{self.major}"

    @property
    def user_agent(self) -> str:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{self.major}.0.0.0 Safari/537.36"
        )

    @property
    def sec_ch_ua(self) -> str:
        return (
            f'"Not_A Brand";v="99", "Google Chrome";v="{self.major}", '
            f'"Chromium";v="{self.major}"'
        )

    def headers(self, opname: str, attempt: int = 0, *, referer: str) -> dict[str, str]:
        """Full gateway headers for one operation attempt.

        Origin and Referer are always present (a missing Referer alone
        was measured to return 206). No Cookie header is ever emitted.
        """
        attempts = OP_HEADER_ATTEMPTS.get(opname) or (("general-merchandise", "general-merchandise"),)
        experience, client_name = attempts[min(attempt, len(attempts) - 1)]
        return {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": "https://www.homedepot.com",
            "Referer": referer,
            "User-Agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-experience-name": experience,
            "apollographql-client-name": client_name,
        }


def identity_for(major: int) -> HdIdentity:
    return HdIdentity(major=int(major))


def direct_lane_enabled() -> bool:
    """Kill switch: SNIPERPLUG_HD_DIRECT_ENABLED=false disables the lane."""
    return (os.getenv("SNIPERPLUG_HD_DIRECT_ENABLED") or "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
