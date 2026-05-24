from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    discord_token: str
    database_path: str = "./data/sniperplug.sqlite3"
    dev_guild_id: int | None = None
    dev_guild_ids: tuple[int, ...] = ()
    sync_global_commands: bool = False
    bestbuy_api_key: str | None = None
    walmart_consumer_id: str | None = None
    walmart_key_version: str | None = None
    walmart_private_key_b64: str | None = None
    walmart_publisher_id: str | None = None
    walmart_provider_enabled: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Missing DISCORD_TOKEN in environment or .env file.")

        raw_guild_id = os.getenv("DEV_GUILD_ID", "").strip()
        dev_guild_id = int(raw_guild_id) if raw_guild_id.isdigit() else None

        raw_guild_ids = os.getenv("DEV_GUILD_IDS", "").strip()
        dev_guild_ids = parse_guild_ids(raw_guild_ids)
        if dev_guild_id and dev_guild_id not in dev_guild_ids:
            dev_guild_ids = (dev_guild_id, *dev_guild_ids)

        sync_global_commands = os.getenv("SYNC_GLOBAL_COMMANDS", "").strip().lower() in {"1", "true", "yes", "on"}

        bestbuy_api_key = os.getenv("BESTBUY_API_KEY", "").strip() or None

        walmart_consumer_id = os.getenv("WALMART_CONSUMER_ID", "").strip() or None
        walmart_key_version = os.getenv("WALMART_KEY_VERSION", "1").strip() or "1"
        walmart_private_key_b64 = os.getenv("WALMART_PRIVATE_KEY_B64", "").strip() or None
        walmart_publisher_id = os.getenv("WALMART_PUBLISHER_ID", "").strip() or None
        walmart_provider_enabled = os.getenv("WALMART_PROVIDER_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            discord_token=token,
            database_path=os.getenv("DATABASE_PATH", "./data/sniperplug.sqlite3").strip(),
            dev_guild_id=dev_guild_id,
            dev_guild_ids=dev_guild_ids,
            sync_global_commands=sync_global_commands,
            bestbuy_api_key=bestbuy_api_key,
            walmart_consumer_id=walmart_consumer_id,
            walmart_key_version=walmart_key_version,
            walmart_private_key_b64=walmart_private_key_b64,
            walmart_publisher_id=walmart_publisher_id,
            walmart_provider_enabled=walmart_provider_enabled,
        )


def parse_guild_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        value = part.strip()
        if value.isdigit():
            guild_id = int(value)
            if guild_id not in ids:
                ids.append(guild_id)
    return tuple(ids)
