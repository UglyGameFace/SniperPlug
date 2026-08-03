from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv

from sniperplug.config import env_bool


DEFAULT_SITEMAP_INDEX = "https://www.target.com/sitemap_pdp-index.xml.gz"
DEFAULT_REDSKY_BASE_URL = "https://redsky.target.com/redsky_aggregations/v1/web"


@dataclass(frozen=True)
class TargetWatcherSettings:
    database_path: str = "./data/sniperplug.sqlite3"
    sitemap_index_url: str = DEFAULT_SITEMAP_INDEX
    redsky_base_url: str = DEFAULT_REDSKY_BASE_URL
    redsky_api_key: str = ""
    watch_tcins: tuple[str, ...] = ()
    loop_seconds: int = 15
    sitemap_batch_size: int = 2
    product_batch_size: int = 20
    locations_per_cycle: int = 2
    products_per_location_batch: int = 20
    location_scan_spacing_seconds: int = 15
    request_concurrency: int = 3
    request_timeout_seconds: float = 20.0
    sitemap_max_compressed_bytes: int = 25 * 1024 * 1024
    sitemap_max_expanded_bytes: int = 100 * 1024 * 1024
    min_event_discount_percent: int = 10
    normal_offer_interval_minutes: int = 30
    markdown_offer_interval_seconds: int = 90
    sitemap_refresh_minutes: int = 30
    big_ticket_min_reference_price: float = 200.0
    price_error_min_discount_percent: int = 69
    big_ticket_offer_interval_seconds: int = 45
    require_remote_database: bool = True
    run_once: bool = False
    user_agent: str = "SniperPlug-Target-Watcher/1.0 (+https://sniperplug.com)"

    @classmethod
    def from_env(cls) -> "TargetWatcherSettings":
        load_dotenv()
        return cls(
            database_path=os.getenv("DATABASE_PATH", "./data/sniperplug.sqlite3").strip(),
            sitemap_index_url=os.getenv(
                "TARGET_SITEMAP_INDEX_URL", DEFAULT_SITEMAP_INDEX
            ).strip(),
            redsky_base_url=os.getenv(
                "TARGET_REDSKY_BASE_URL", DEFAULT_REDSKY_BASE_URL
            ).strip(),
            redsky_api_key=os.getenv("TARGET_REDSKY_API_KEY", "").strip(),
            watch_tcins=_tcin_list(os.getenv("TARGET_WATCH_TCINS", "")),
            loop_seconds=_bounded_int("TARGET_WATCHER_LOOP_SECONDS", 15, 10, 3600),
            sitemap_batch_size=_bounded_int("TARGET_SITEMAP_BATCH_SIZE", 2, 1, 20),
            product_batch_size=_bounded_int("TARGET_PRODUCT_BATCH_SIZE", 20, 1, 100),
            locations_per_cycle=_bounded_int(
                "TARGET_LOCATIONS_PER_CYCLE", 2, 1, 25
            ),
            products_per_location_batch=_bounded_int(
                "TARGET_PRODUCTS_PER_LOCATION_BATCH", 20, 1, 100
            ),
            location_scan_spacing_seconds=_bounded_int(
                "TARGET_LOCATION_SCAN_SPACING_SECONDS", 15, 10, 3600
            ),
            request_concurrency=_bounded_int("TARGET_REQUEST_CONCURRENCY", 3, 1, 8),
            request_timeout_seconds=_bounded_float(
                "TARGET_REQUEST_TIMEOUT_SECONDS", 20.0, 3.0, 90.0
            ),
            sitemap_max_compressed_bytes=_bounded_int(
                "TARGET_SITEMAP_MAX_COMPRESSED_BYTES",
                25 * 1024 * 1024,
                1024 * 1024,
                100 * 1024 * 1024,
            ),
            sitemap_max_expanded_bytes=_bounded_int(
                "TARGET_SITEMAP_MAX_EXPANDED_BYTES",
                100 * 1024 * 1024,
                5 * 1024 * 1024,
                300 * 1024 * 1024,
            ),
            min_event_discount_percent=_bounded_int(
                "TARGET_MIN_EVENT_DISCOUNT_PERCENT", 10, 1, 95
            ),
            normal_offer_interval_minutes=_bounded_int(
                "TARGET_NORMAL_OFFER_INTERVAL_MINUTES", 30, 5, 1440
            ),
            markdown_offer_interval_seconds=_bounded_int(
                "TARGET_MARKDOWN_OFFER_INTERVAL_SECONDS", 90, 30, 3600
            ),
            sitemap_refresh_minutes=_bounded_int(
                "TARGET_SITEMAP_REFRESH_MINUTES", 30, 5, 1440
            ),
            big_ticket_min_reference_price=_bounded_float(
                "TARGET_BIG_TICKET_MIN_REFERENCE_PRICE", 200.0, 100.0, 10000.0
            ),
            price_error_min_discount_percent=_bounded_int(
                "TARGET_PRICE_ERROR_MIN_DISCOUNT_PERCENT", 69, 50, 95
            ),
            big_ticket_offer_interval_seconds=_bounded_int(
                "TARGET_BIG_TICKET_OFFER_INTERVAL_SECONDS", 45, 30, 600
            ),
            require_remote_database=env_bool(
                "TARGET_WATCHER_REQUIRE_REMOTE_DB", default=True
            ),
            run_once=env_bool("TARGET_WATCHER_RUN_ONCE", default=False),
            user_agent=os.getenv(
                "TARGET_WATCHER_USER_AGENT",
                "SniperPlug-Target-Watcher/1.0 (+https://sniperplug.com)",
            ).strip(),
        )

    def validate_runtime(self) -> None:
        if self.sitemap_index_url != DEFAULT_SITEMAP_INDEX:
            if not self.sitemap_index_url.startswith("https://www.target.com/"):
                raise RuntimeError(
                    "TARGET_SITEMAP_INDEX_URL must be an official https://www.target.com/ URL."
                )
        if self.redsky_base_url.rstrip("/") != DEFAULT_REDSKY_BASE_URL:
            raise RuntimeError(
                "TARGET_REDSKY_BASE_URL must use Target's official RedSky web aggregation origin."
            )
        if not self.redsky_api_key:
            raise RuntimeError(
                "TARGET_REDSKY_API_KEY is required and must be provided as a deployment secret."
            )
        if self.require_remote_database:
            remote_url = os.getenv("TURSO_DATABASE_URL", "").strip() or os.getenv(
                "LIBSQL_URL", ""
            ).strip()
            remote_token = os.getenv("TURSO_AUTH_TOKEN", "").strip() or os.getenv(
                "LIBSQL_AUTH_TOKEN", ""
            ).strip()
            if not remote_url or not remote_token:
                raise RuntimeError(
                    "The standalone Target watcher must share SniperPlug's Turso/libSQL database. "
                    "Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN, or set "
                    "TARGET_WATCHER_REQUIRE_REMOTE_DB=false only for local tests."
                )


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


def _tcin_list(value: str) -> tuple[str, ...]:
    values: list[str] = []
    for piece in str(value or "").replace(";", ",").split(","):
        tcin = piece.strip()
        if tcin.isdigit() and 5 <= len(tcin) <= 12 and tcin not in values:
            values.append(tcin)
    return tuple(values)
