from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    discord_token: str
    database_path: str = "./data/sniperplug.sqlite3"
    dev_guild_id: int | None = None
    bestbuy_api_key: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Missing DISCORD_TOKEN in environment or .env file.")

        raw_guild_id = os.getenv("DEV_GUILD_ID", "").strip()
        dev_guild_id = int(raw_guild_id) if raw_guild_id.isdigit() else None

        bestbuy_api_key = os.getenv("BESTBUY_API_KEY", "").strip() or None

        return cls(
            discord_token=token,
            database_path=os.getenv("DATABASE_PATH", "./data/sniperplug.sqlite3").strip(),
            dev_guild_id=dev_guild_id,
            bestbuy_api_key=bestbuy_api_key,
        )
