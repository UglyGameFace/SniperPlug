from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv

from sniperplug.config import env_bool


DEFAULT_SITEMAP_INDEX = "https://www.hp.com/sitemap-index-store-us-en.xml"
DEFAULT_PRICE_ENDPOINT = "https://www.hp.com/webapp/wcs/stores/servlet/HPServices"


@dataclass(frozen=True)
class HPWatcherSettings:
    database_path: str = "./data/sniperplug.sqlite3"
    sitemap_index_url: str = DEFAULT_SITEMAP_INDEX
    price_endpoint_url: str = DEFAULT_PRICE_ENDPOINT
    loop_seconds: int = 10
    sitemap_batch_size: int = 2
    product_page_batch_size: int = 24
    offer_batch_size: int = 80
    request_concurrency: int = 3
    request_timeout_seconds: float = 20.0
    min_event_discount_percent: int = 10
    normal_offer_interval_minutes: int = 30
    markdown_offer_interval_seconds: int = 90
    product_page_refresh_hours: int = 24
    sitemap_refresh_minutes: int = 10
    big_ticket_min_reference_price: float = 200.0
    price_error_min_discount_percent: int = 69
    big_ticket_offer_interval_seconds: int = 45
    require_remote_database: bool = True
    run_once: bool = False
    user_agent: str = "SniperPlug-HP-Watcher/1.1 (+https://sniperplug.com)"

    @classmethod
    def from_env(cls) -> "HPWatcherSettings":
        load_dotenv()
        return cls(
            database_path=os.getenv("DATABASE_PATH", "./data/sniperplug.sqlite3").strip(),
            sitemap_index_url=os.getenv("HP_SITEMAP_INDEX_URL", DEFAULT_SITEMAP_INDEX).strip(),
            price_endpoint_url=os.getenv("HP_PRICE_ENDPOINT_URL", DEFAULT_PRICE_ENDPOINT).strip(),
            loop_seconds=_bounded_int("HP_WATCHER_LOOP_SECONDS", 10, minimum=10, maximum=3600),
            sitemap_batch_size=_bounded_int("HP_SITEMAP_BATCH_SIZE", 2, minimum=1, maximum=20),
            product_page_batch_size=_bounded_int("HP_PRODUCT_PAGE_BATCH_SIZE", 24, minimum=1, maximum=100),
            offer_batch_size=_bounded_int("HP_OFFER_BATCH_SIZE", 80, minimum=1, maximum=100),
            request_concurrency=_bounded_int("HP_REQUEST_CONCURRENCY", 3, minimum=1, maximum=8),
            request_timeout_seconds=_bounded_float("HP_REQUEST_TIMEOUT_SECONDS", 20.0, minimum=3.0, maximum=90.0),
            min_event_discount_percent=_bounded_int("HP_MIN_EVENT_DISCOUNT_PERCENT", 10, minimum=1, maximum=95),
            normal_offer_interval_minutes=_bounded_int("HP_NORMAL_OFFER_INTERVAL_MINUTES", 30, minimum=5, maximum=1440),
            markdown_offer_interval_seconds=_bounded_int("HP_MARKDOWN_OFFER_INTERVAL_SECONDS", 90, minimum=30, maximum=3600),
            product_page_refresh_hours=_bounded_int("HP_PRODUCT_PAGE_REFRESH_HOURS", 24, minimum=1, maximum=720),
            sitemap_refresh_minutes=_bounded_int("HP_SITEMAP_REFRESH_MINUTES", 10, minimum=2, maximum=1440),
            big_ticket_min_reference_price=_bounded_float(
                "HP_BIG_TICKET_MIN_REFERENCE_PRICE",
                200.0,
                minimum=100.0,
                maximum=10000.0,
            ),
            price_error_min_discount_percent=_bounded_int(
                "HP_PRICE_ERROR_MIN_DISCOUNT_PERCENT",
                69,
                minimum=50,
                maximum=95,
            ),
            big_ticket_offer_interval_seconds=_bounded_int(
                "HP_BIG_TICKET_OFFER_INTERVAL_SECONDS",
                45,
                minimum=30,
                maximum=600,
            ),
            require_remote_database=env_bool("HP_WATCHER_REQUIRE_REMOTE_DB", default=True),
            run_once=env_bool("HP_WATCHER_RUN_ONCE", default=False),
            user_agent=os.getenv("HP_WATCHER_USER_AGENT", "SniperPlug-HP-Watcher/1.1 (+https://sniperplug.com)").strip(),
        )

    def validate_runtime(self) -> None:
        if not self.sitemap_index_url.startswith("https://www.hp.com/"):
            raise RuntimeError("HP_SITEMAP_INDEX_URL must be an official https://www.hp.com/ URL.")
        if not self.price_endpoint_url.startswith("https://www.hp.com/"):
            raise RuntimeError("HP_PRICE_ENDPOINT_URL must be an official https://www.hp.com/ URL.")
        if self.require_remote_database:
            remote_url = os.getenv("TURSO_DATABASE_URL", "").strip() or os.getenv("LIBSQL_URL", "").strip()
            remote_token = os.getenv("TURSO_AUTH_TOKEN", "").strip() or os.getenv("LIBSQL_AUTH_TOKEN", "").strip()
            if not remote_url or not remote_token:
                raise RuntimeError(
                    "The standalone HP watcher must share SniperPlug's Turso/libSQL database in production. "
                    "Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN, or set HP_WATCHER_REQUIRE_REMOTE_DB=false only for local tests."
                )


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(minimum, min(maximum, value))


def _bounded_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float(default)
    return max(minimum, min(maximum, value))
