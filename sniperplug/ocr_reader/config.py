from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv

from sniperplug.config import env_bool


@dataclass(frozen=True)
class OcrReaderSettings:
    database_path: str = "./data/sniperplug.sqlite3"
    poll_seconds: int = 3
    heartbeat_seconds: int = 30
    job_lease_seconds: int = 180
    job_timeout_seconds: int = 150
    require_remote_database: bool = True
    user_agent: str = "SniperPlug-Reader/1.0 (+https://sniperplug.com)"

    @classmethod
    def from_env(cls) -> "OcrReaderSettings":
        load_dotenv()
        return cls(
            database_path=os.getenv("DATABASE_PATH", "./data/sniperplug.sqlite3").strip(),
            poll_seconds=_bounded_int("OCR_READER_POLL_SECONDS", 3, minimum=1, maximum=60),
            heartbeat_seconds=_bounded_int("OCR_READER_HEARTBEAT_SECONDS", 30, minimum=5, maximum=120),
            job_lease_seconds=_bounded_int("OCR_READER_JOB_LEASE_SECONDS", 180, minimum=30, maximum=900),
            job_timeout_seconds=_bounded_int("OCR_READER_JOB_TIMEOUT_SECONDS", 150, minimum=30, maximum=600),
            require_remote_database=env_bool("OCR_READER_REQUIRE_REMOTE_DB", default=True),
            user_agent=os.getenv("OCR_READER_USER_AGENT", "SniperPlug-Reader/1.0 (+https://sniperplug.com)").strip(),
        )

    def validate_runtime(self) -> None:
        if self.require_remote_database:
            remote_url = os.getenv("TURSO_DATABASE_URL", "").strip() or os.getenv("LIBSQL_URL", "").strip()
            remote_token = os.getenv("TURSO_AUTH_TOKEN", "").strip() or os.getenv("LIBSQL_AUTH_TOKEN", "").strip()
            if not remote_url or not remote_token:
                raise RuntimeError(
                    "The standalone picture reader must share SniperPlug's Turso/libSQL database in production. "
                    "Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN, or set OCR_READER_REQUIRE_REMOTE_DB=false only for local tests."
                )


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else int(default)
    except ValueError:
        value = int(default)
    return max(minimum, min(maximum, value))
