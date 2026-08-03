from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable

from dotenv import load_dotenv

from sniperplug.config import env_bool


DEFAULT_BIG_TICKET_QUERIES = (
    "gaming laptop",
    "desktop workstation",
    "graphics card",
    "mirrorless camera",
    "camera lens",
    "luxury watch",
    "unlocked smartphone",
    "professional audio equipment",
    "power tool combo kit",
)

DEFAULT_SOUGHT_AFTER_QUERIES = (
    "NVIDIA GeForce RTX 5090",
    "NVIDIA GeForce RTX 5080",
    "PlayStation 5 Pro",
    "Nintendo Switch 2",
    "Steam Deck OLED",
    "ROG Ally X",
    "Apple iPhone Pro Max",
    "Apple MacBook Pro",
)

DEFAULT_ALLOWED_CONDITIONS = (
    "new",
    "open_box",
    "certified_refurbished",
    "manufacturer_refurbished",
    "seller_refurbished",
    "used_excellent",
    "used_very_good",
    "used_good",
)


@dataclass(frozen=True)
class EbayWatcherSettings:
    database_path: str = "./data/sniperplug.sqlite3"
    environment: str = "production"
    client_id: str = ""
    client_secret: str = ""
    marketplace_id: str = "EBAY_US"
    buyer_country: str = "US"
    buyer_postal_code: str = ""
    loop_seconds: int = 15
    rule_batch_size: int = 2
    tracked_batch_size: int = 20
    search_limit: int = 100
    request_concurrency: int = 2
    request_timeout_seconds: float = 20.0
    default_rule_interval_seconds: int = 300
    big_ticket_rule_interval_seconds: int = 900
    default_tracked_interval_seconds: int = 900
    background_tracked_interval_seconds: int = 1800
    failure_retry_seconds: int = 120
    default_min_discount_percent: int = 69
    big_ticket_min_reference_price: float = 200.0
    sought_after_min_reference_price: float = 75.0
    minimum_comparables: int = 5
    minimum_baseline_observations: int = 2
    minimum_baseline_age_seconds: int = 240
    minimum_seller_feedback_percentage: float = 97.0
    minimum_seller_feedback_score: int = 10
    allowed_conditions: tuple[str, ...] = DEFAULT_ALLOWED_CONDITIONS
    big_ticket_queries: tuple[str, ...] = DEFAULT_BIG_TICKET_QUERIES
    sought_after_queries: tuple[str, ...] = DEFAULT_SOUGHT_AFTER_QUERIES
    require_remote_database: bool = True
    run_once: bool = False
    user_agent: str = "SniperPlug-eBay-Watcher/1.0 (+https://sniperplug.com)"

    @property
    def api_base_url(self) -> str:
        return (
            "https://api.sandbox.ebay.com"
            if self.environment == "sandbox"
            else "https://api.ebay.com"
        )

    @property
    def identity_base_url(self) -> str:
        return self.api_base_url

    @classmethod
    def from_env(cls) -> "EbayWatcherSettings":
        load_dotenv()
        environment = os.getenv("EBAY_ENVIRONMENT", "production").strip().lower()
        if environment not in {"production", "sandbox"}:
            environment = "production"
        return cls(
            database_path=os.getenv("DATABASE_PATH", "./data/sniperplug.sqlite3").strip(),
            environment=environment,
            client_id=os.getenv("EBAY_CLIENT_ID", "").strip(),
            client_secret=os.getenv("EBAY_CLIENT_SECRET", "").strip(),
            marketplace_id=os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US").strip().upper(),
            buyer_country=os.getenv("EBAY_BUYER_COUNTRY", "US").strip().upper(),
            buyer_postal_code=os.getenv("EBAY_BUYER_POSTAL_CODE", "").strip(),
            loop_seconds=_bounded_int("EBAY_WATCHER_LOOP_SECONDS", 15, 10, 3600),
            rule_batch_size=_bounded_int("EBAY_RULE_BATCH_SIZE", 2, 1, 10),
            tracked_batch_size=_bounded_int("EBAY_TRACKED_BATCH_SIZE", 20, 1, 20),
            search_limit=_bounded_int("EBAY_SEARCH_LIMIT", 100, 10, 200),
            request_concurrency=_bounded_int("EBAY_REQUEST_CONCURRENCY", 2, 1, 5),
            request_timeout_seconds=_bounded_float(
                "EBAY_REQUEST_TIMEOUT_SECONDS", 20.0, 3.0, 90.0
            ),
            default_rule_interval_seconds=_bounded_int(
                "EBAY_RULE_INTERVAL_SECONDS", 300, 60, 86400
            ),
            big_ticket_rule_interval_seconds=_bounded_int(
                "EBAY_BIG_TICKET_RULE_INTERVAL_SECONDS", 900, 60, 86400
            ),
            default_tracked_interval_seconds=_bounded_int(
                "EBAY_TRACKED_INTERVAL_SECONDS", 900, 120, 86400
            ),
            background_tracked_interval_seconds=_bounded_int(
                "EBAY_BACKGROUND_TRACKED_INTERVAL_SECONDS", 1800, 300, 172800
            ),
            failure_retry_seconds=_bounded_int(
                "EBAY_FAILURE_RETRY_SECONDS", 120, 30, 3600
            ),
            default_min_discount_percent=_bounded_int(
                "EBAY_MIN_DISCOUNT_PERCENT", 69, 1, 95
            ),
            big_ticket_min_reference_price=_bounded_float(
                "EBAY_BIG_TICKET_MIN_REFERENCE_PRICE", 200.0, 25.0, 100000.0
            ),
            sought_after_min_reference_price=_bounded_float(
                "EBAY_SOUGHT_AFTER_MIN_REFERENCE_PRICE", 75.0, 10.0, 100000.0
            ),
            minimum_comparables=_bounded_int(
                "EBAY_MINIMUM_COMPARABLES", 5, 3, 50
            ),
            minimum_baseline_observations=_bounded_int(
                "EBAY_MINIMUM_BASELINE_OBSERVATIONS", 2, 2, 20
            ),
            minimum_baseline_age_seconds=_bounded_int(
                "EBAY_MINIMUM_BASELINE_AGE_SECONDS", 240, 0, 86400
            ),
            minimum_seller_feedback_percentage=_bounded_float(
                "EBAY_MIN_SELLER_FEEDBACK_PERCENTAGE", 97.0, 0.0, 100.0
            ),
            minimum_seller_feedback_score=_bounded_int(
                "EBAY_MIN_SELLER_FEEDBACK_SCORE", 10, 0, 100000000
            ),
            allowed_conditions=_csv_tuple(
                os.getenv("EBAY_ALLOWED_CONDITIONS"),
                DEFAULT_ALLOWED_CONDITIONS,
            ),
            big_ticket_queries=_csv_tuple(
                os.getenv("EBAY_BIG_TICKET_QUERIES"),
                DEFAULT_BIG_TICKET_QUERIES,
            ),
            sought_after_queries=_csv_tuple(
                os.getenv("EBAY_SOUGHT_AFTER_QUERIES"),
                DEFAULT_SOUGHT_AFTER_QUERIES,
            ),
            require_remote_database=env_bool(
                "EBAY_WATCHER_REQUIRE_REMOTE_DB", default=True
            ),
            run_once=env_bool("EBAY_WATCHER_RUN_ONCE", default=False),
            user_agent=os.getenv(
                "EBAY_WATCHER_USER_AGENT",
                "SniperPlug-eBay-Watcher/1.0 (+https://sniperplug.com)",
            ).strip(),
        )

    def validate_runtime(self) -> None:
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are required for the eBay watcher."
            )
        if self.marketplace_id != "EBAY_US":
            raise RuntimeError(
                "The first eBay watcher release is intentionally locked to EBAY_US."
            )
        if self.buyer_country != "US":
            raise RuntimeError(
                "The first eBay watcher release requires EBAY_BUYER_COUNTRY=US."
            )
        if self.require_remote_database:
            remote_url = (
                os.getenv("TURSO_DATABASE_URL", "").strip()
                or os.getenv("LIBSQL_URL", "").strip()
            )
            remote_token = (
                os.getenv("TURSO_AUTH_TOKEN", "").strip()
                or os.getenv("LIBSQL_AUTH_TOKEN", "").strip()
            )
            if not remote_url or not remote_token:
                raise RuntimeError(
                    "The standalone eBay watcher must share SniperPlug's Turso/libSQL "
                    "database. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN, or set "
                    "EBAY_WATCHER_REQUIRE_REMOTE_DB=false only for local tests."
                )


def _csv_tuple(value: str | None, default: Iterable[str]) -> tuple[str, ...]:
    if value is None or not value.strip():
        return tuple(default)
    parts = [
        " ".join(piece.strip().split())
        for piece in value.replace("\n", ",").replace(";", ",").split(",")
    ]
    return tuple(dict.fromkeys(piece for piece in parts if piece))


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(minimum, min(maximum, value))


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float(default)
    return max(minimum, min(maximum, value))
