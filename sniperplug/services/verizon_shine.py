from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Iterable

ET = ZoneInfo("America/New_York")

DEFAULT_PRIORITY_KEYWORDS = (
    "gift card",
    "daily drop",
    "epic wins",
    "presale",
    "ticket",
    "tickets",
    "fifa",
    "sweepstakes",
    "merch",
    "local pass",
)

DEFAULT_REMINDER_OFFSETS = (30, 10, 1)

RELEVANCE_TERMS = (
    "verizon",
    "shine",
    "myaccess",
    "my access",
    "reward",
    "daily drop",
    "epic wins",
    "presale",
    "ticket",
    "gift card",
    "fifa",
    "sweepstakes",
)


@dataclass(slots=True)
class VerizonShineConfig:
    guild_id: int
    alert_channel_id: int | None = None
    enabled: bool = False
    reminders_enabled: bool = True
    reminder_offsets: tuple[int, ...] = DEFAULT_REMINDER_OFFSETS
    priority_keywords: tuple[str, ...] = DEFAULT_PRIORITY_KEYWORDS
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


@dataclass(slots=True)
class VerizonShineReward:
    guild_id: int
    reward_id: str
    title: str
    reward_type: str
    status: str
    source: str
    first_seen_at: str
    last_seen_at: str
    available_at: str | None = None
    expires_at: str | None = None
    priority: str = "normal"
    raw_text: str = ""
    fingerprint_hash: str = ""


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip().lower())


def normalize_title(title: str) -> str:
    value = normalize_text(title)
    value = re.sub(r"[^a-z0-9$% ]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def fingerprint(title: str, status: str, source: str) -> str:
    body = f"{normalize_title(title)}|{normalize_text(status)}|{normalize_text(source)}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def reward_id_for(guild_id: int, title: str, source: str) -> str:
    body = f"{guild_id}|{normalize_title(title)}|{normalize_text(source)}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def priority_for(text: str, keywords: Iterable[str]) -> str:
    haystack = normalize_text(text)
    for keyword in keywords:
        if normalize_text(keyword) and normalize_text(keyword) in haystack:
            return "high"
    return "normal"


def classify_type(text: str) -> str:
    haystack = normalize_text(text)
    if "daily drop" in haystack:
        return "Daily Drop"
    if "epic wins" in haystack or "epic win" in haystack:
        return "Epic Wins"
    if "presale" in haystack or "pre-sale" in haystack:
        return "Presale"
    if "ticket" in haystack or "local pass" in haystack:
        return "Ticket Access"
    if "gift card" in haystack or "egift" in haystack or "e-gift" in haystack:
        return "Gift Card"
    if "merch" in haystack or "hoodie" in haystack or "shirt" in haystack:
        return "Merch"
    return "Reward"


def classify_status(text: str) -> str:
    haystack = normalize_text(text)
    if any(term in haystack for term in ("sold out", "claimed", "all gone", "no longer available")):
        return "sold_out"
    if any(term in haystack for term in ("expired", "ended", "closed")):
        return "expired"
    if any(term in haystack for term in ("available now", "claim now", "open now", "live now")):
        return "available"
    if any(term in haystack for term in ("coming soon", "starts", "opens", "available in", "drop")):
        return "coming_soon"
    return "unknown"


def is_relevant_notification(title: str, body: str) -> bool:
    haystack = normalize_text(f"{title}\n{body}")
    return any(term in haystack for term in RELEVANCE_TERMS)


def parse_datetime_hint(text: str, *, now: datetime | None = None) -> datetime | None:
    now = now or utc_now()
    haystack = text.strip()
    lower = haystack.lower()

    iso_match = re.search(r"(\d{4}-\d{2}-\d{2})(?:[ t](\d{1,2}:\d{2})(?::\d{2})?)?", haystack)
    if iso_match:
        date_part = iso_match.group(1)
        time_part = iso_match.group(2) or "00:00"
        try:
            dt = datetime.fromisoformat(f"{date_part}T{time_part}")
            return dt.replace(tzinfo=ET).astimezone(UTC)
        except ValueError:
            pass

    countdown = re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", lower)
    if countdown and any(term in lower for term in ("countdown", "available in", "starts in", "opens in")):
        hours = int(countdown.group(1))
        minutes = int(countdown.group(2))
        seconds = int(countdown.group(3) or 0)
        return now + timedelta(hours=hours, minutes=minutes, seconds=seconds)

    relative = re.search(r"\bin\s+(\d{1,4})\s*(minute|minutes|min|mins|m|hour|hours|hr|hrs|h)\b", lower)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if unit.startswith("h"):
            return now + timedelta(hours=amount)
        return now + timedelta(minutes=amount)

    time_match = re.search(r"\b(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:et|est|edt)?\b", lower)
    if not time_match:
        return None

    hour = int(time_match.group(1))
    minute = int(time_match.group(2) or 0)
    meridiem = time_match.group(3)
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0

    now_et = now.astimezone(ET)
    target_date = now_et.date()

    if "tomorrow" in lower:
        target_date += timedelta(days=1)
    elif "today" in lower:
        pass
    else:
        weekday_names = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        for name, weekday in weekday_names.items():
            if name in lower:
                days_ahead = (weekday - now_et.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                target_date += timedelta(days=days_ahead)
                break

    target = datetime.combine(target_date, time(hour, minute), tzinfo=ET)
    if target <= now_et and "today" not in lower and "tomorrow" not in lower:
        target += timedelta(days=1)
    return target.astimezone(UTC)


def split_reward_blocks(text: str) -> list[str]:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return []
    blocks = [part.strip() for part in re.split(r"\n\s*\n|^-{3,}$", cleaned, flags=re.MULTILINE) if part.strip()]
    if len(blocks) == 1:
        return blocks
    return blocks


def parse_rewards_from_text(
    guild_id: int,
    text: str,
    *,
    source: str = "manual",
    keywords: Iterable[str] = DEFAULT_PRIORITY_KEYWORDS,
    now: datetime | None = None,
) -> list[VerizonShineReward]:
    now_dt = now or utc_now()
    seen_at = now_dt.isoformat()
    rewards: list[VerizonShineReward] = []

    for block in split_reward_blocks(text):
        lines = [line.strip("•- \t") for line in block.splitlines() if line.strip("•- \t")]
        if not lines:
            continue

        title = _best_title(lines)
        body = "\n".join(lines)
        available_at = parse_datetime_hint(body, now=now_dt)
        expires_at = _parse_expiry(body, now=now_dt)
        status = classify_status(body)
        reward_type = classify_type(body)
        priority = priority_for(body, keywords)
        fp = fingerprint(title, status, source)
        reward = VerizonShineReward(
            guild_id=guild_id,
            reward_id=reward_id_for(guild_id, title, source),
            title=title,
            reward_type=reward_type,
            status=status,
            source=source,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            available_at=available_at.isoformat() if available_at else None,
            expires_at=expires_at.isoformat() if expires_at else None,
            priority=priority,
            raw_text=body,
            fingerprint_hash=fp,
        )
        rewards.append(reward)

    return rewards


def _best_title(lines: list[str]) -> str:
    for line in lines:
        low = normalize_text(line)
        if any(skip in low for skip in ("available in", "countdown", "expires", "opens at", "starts at")):
            continue
        return line[:160]
    return lines[0][:160]


def _parse_expiry(text: str, *, now: datetime) -> datetime | None:
    expiry_lines = [
        line for line in text.splitlines()
        if any(term in normalize_text(line) for term in ("expires", "ends", "last day"))
    ]
    for line in expiry_lines:
        parsed = parse_datetime_hint(line, now=now)
        if parsed:
            return parsed
    return None


def should_alert(
    existing: VerizonShineReward | None,
    incoming: VerizonShineReward,
    config: VerizonShineConfig,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    now = now or utc_now()
    if existing is None:
        return True, "new reward"

    if existing.status != incoming.status:
        return True, f"status changed: {existing.status} → {incoming.status}"

    if existing.priority != "high" and incoming.priority == "high":
        return True, "priority keyword matched"

    old_available = parse_iso_datetime(existing.available_at)
    new_available = parse_iso_datetime(incoming.available_at)
    if new_available and old_available != new_available:
        minutes_until = int((new_available - now).total_seconds() // 60)
        if any(0 <= minutes_until <= offset for offset in config.reminder_offsets):
            return True, "timer moved into reminder window"

    if existing.fingerprint_hash != incoming.fingerprint_hash and incoming.status == "available":
        return True, "available reward changed"

    return False, "duplicate suppressed"


class VerizonShineStore:
    def __init__(self, db: Any):
        self.db = db
        self._schema_ready = False

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        conn = self.db.require_conn()
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS verizon_shine_config (
                guild_id INTEGER PRIMARY KEY,
                alert_channel_id INTEGER,
                enabled INTEGER NOT NULL DEFAULT 0,
                reminders_enabled INTEGER NOT NULL DEFAULT 1,
                reminder_offsets_json TEXT NOT NULL DEFAULT '[30,10,1]',
                priority_keywords_json TEXT NOT NULL DEFAULT '["gift card","daily drop","epic wins","presale","ticket","tickets","fifa","sweepstakes","merch","local pass"]',
                quiet_hours_start TEXT,
                quiet_hours_end TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS verizon_shine_rewards (
                guild_id INTEGER NOT NULL,
                reward_id TEXT NOT NULL,
                title TEXT NOT NULL,
                reward_type TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                available_at TEXT,
                expires_at TEXT,
                priority TEXT NOT NULL DEFAULT 'normal',
                raw_text TEXT NOT NULL DEFAULT '',
                fingerprint_hash TEXT NOT NULL,
                PRIMARY KEY (guild_id, reward_id)
            );

            CREATE TABLE IF NOT EXISTS verizon_shine_reminders (
                guild_id INTEGER NOT NULL,
                reward_id TEXT NOT NULL,
                offset_minutes INTEGER NOT NULL,
                remind_at TEXT NOT NULL,
                sent_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, reward_id, offset_minutes)
            );

            CREATE INDEX IF NOT EXISTS idx_verizon_shine_rewards_guild_seen
                ON verizon_shine_rewards(guild_id, last_seen_at DESC);
            CREATE INDEX IF NOT EXISTS idx_verizon_shine_reminders_due
                ON verizon_shine_reminders(remind_at, sent_at);
            """
        )
        await conn.commit()
        self._schema_ready = True

    async def get_config(self, guild_id: int) -> VerizonShineConfig:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute("SELECT * FROM verizon_shine_config WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()
        if not row:
            return VerizonShineConfig(guild_id=guild_id)
        return config_from_row(row)

    async def save_config(self, config: VerizonShineConfig) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        now = utc_now_iso()
        await conn.execute(
            """
            INSERT INTO verizon_shine_config (
                guild_id, alert_channel_id, enabled, reminders_enabled,
                reminder_offsets_json, priority_keywords_json,
                quiet_hours_start, quiet_hours_end, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                alert_channel_id = excluded.alert_channel_id,
                enabled = excluded.enabled,
                reminders_enabled = excluded.reminders_enabled,
                reminder_offsets_json = excluded.reminder_offsets_json,
                priority_keywords_json = excluded.priority_keywords_json,
                quiet_hours_start = excluded.quiet_hours_start,
                quiet_hours_end = excluded.quiet_hours_end,
                updated_at = excluded.updated_at
            """,
            (
                config.guild_id,
                config.alert_channel_id,
                int(config.enabled),
                int(config.reminders_enabled),
                json.dumps(list(config.reminder_offsets)),
                json.dumps(list(config.priority_keywords)),
                config.quiet_hours_start,
                config.quiet_hours_end,
                now,
                now,
            ),
        )
        await conn.commit()

    async def add_keyword(self, guild_id: int, keyword: str) -> tuple[VerizonShineConfig, bool]:
        config = await self.get_config(guild_id)
        normalized = normalize_text(keyword)
        keywords = list(config.priority_keywords)
        if any(normalize_text(existing) == normalized for existing in keywords):
            return config, False
        keywords.append(keyword.strip())
        config.priority_keywords = tuple(keywords)
        await self.save_config(config)
        return config, True

    async def remove_keyword(self, guild_id: int, keyword: str) -> tuple[VerizonShineConfig, bool]:
        config = await self.get_config(guild_id)
        normalized = normalize_text(keyword)
        kept = [existing for existing in config.priority_keywords if normalize_text(existing) != normalized]
        if len(kept) == len(config.priority_keywords):
            return config, False
        config.priority_keywords = tuple(kept)
        await self.save_config(config)
        return config, True

    async def get_reward(self, guild_id: int, reward_id: str) -> VerizonShineReward | None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            "SELECT * FROM verizon_shine_rewards WHERE guild_id = ? AND reward_id = ?",
            (guild_id, reward_id),
        )
        row = await cursor.fetchone()
        return reward_from_row(row) if row else None

    async def upsert_reward(self, reward: VerizonShineReward) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        await conn.execute(
            """
            INSERT INTO verizon_shine_rewards (
                guild_id, reward_id, title, reward_type, status, source,
                first_seen_at, last_seen_at, available_at, expires_at,
                priority, raw_text, fingerprint_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, reward_id) DO UPDATE SET
                title = excluded.title,
                reward_type = excluded.reward_type,
                status = excluded.status,
                source = excluded.source,
                last_seen_at = excluded.last_seen_at,
                available_at = excluded.available_at,
                expires_at = excluded.expires_at,
                priority = excluded.priority,
                raw_text = excluded.raw_text,
                fingerprint_hash = excluded.fingerprint_hash
            """,
            (
                reward.guild_id,
                reward.reward_id,
                reward.title,
                reward.reward_type,
                reward.status,
                reward.source,
                reward.first_seen_at,
                reward.last_seen_at,
                reward.available_at,
                reward.expires_at,
                reward.priority,
                reward.raw_text,
                reward.fingerprint_hash,
            ),
        )
        await conn.commit()

    async def list_rewards(self, guild_id: int, *, limit: int = 10) -> list[VerizonShineReward]:
        await self.ensure_schema()
        conn = self.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT * FROM verizon_shine_rewards
            WHERE guild_id = ?
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        rows = await cursor.fetchall()
        return [reward_from_row(row) for row in rows]

    async def save_reminders(self, reward: VerizonShineReward, offsets: Iterable[int]) -> int:
        available_at = parse_iso_datetime(reward.available_at)
        if not available_at:
            return 0
        await self.ensure_schema()
        conn = self.db.require_conn()
        now = utc_now()
        created = 0
        for offset in offsets:
            remind_at = available_at - timedelta(minutes=int(offset))
            if remind_at <= now:
                continue
            await conn.execute(
                """
                INSERT OR IGNORE INTO verizon_shine_reminders (
                    guild_id, reward_id, offset_minutes, remind_at, sent_at, created_at
                )
                VALUES (?, ?, ?, ?, NULL, ?)
                """,
                (reward.guild_id, reward.reward_id, int(offset), remind_at.isoformat(), now.isoformat()),
            )
            created += 1
        await conn.commit()
        return created

    async def due_reminders(self, *, now: datetime | None = None, limit: int = 25) -> list[dict[str, Any]]:
        await self.ensure_schema()
        conn = self.db.require_conn()
        now = now or utc_now()
        cursor = await conn.execute(
            """
            SELECT r.guild_id, r.reward_id, r.offset_minutes, r.remind_at,
                   rewards.title, rewards.reward_type, rewards.status, rewards.source,
                   rewards.first_seen_at, rewards.last_seen_at, rewards.available_at,
                   rewards.expires_at, rewards.priority, rewards.raw_text, rewards.fingerprint_hash
            FROM verizon_shine_reminders r
            JOIN verizon_shine_rewards rewards
              ON rewards.guild_id = r.guild_id AND rewards.reward_id = r.reward_id
            WHERE r.sent_at IS NULL AND r.remind_at <= ?
            ORDER BY r.remind_at ASC
            LIMIT ?
            """,
            (now.isoformat(), limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_reminder_sent(self, guild_id: int, reward_id: str, offset_minutes: int) -> None:
        await self.ensure_schema()
        conn = self.db.require_conn()
        await conn.execute(
            """
            UPDATE verizon_shine_reminders
            SET sent_at = ?
            WHERE guild_id = ? AND reward_id = ? AND offset_minutes = ?
            """,
            (utc_now_iso(), guild_id, reward_id, int(offset_minutes)),
        )
        await conn.commit()


def config_from_row(row: Any) -> VerizonShineConfig:
    return VerizonShineConfig(
        guild_id=int(row["guild_id"]),
        alert_channel_id=int(row["alert_channel_id"]) if row["alert_channel_id"] else None,
        enabled=bool(row["enabled"]),
        reminders_enabled=bool(row["reminders_enabled"]),
        reminder_offsets=tuple(int(value) for value in json.loads(row["reminder_offsets_json"] or "[]")),
        priority_keywords=tuple(str(value) for value in json.loads(row["priority_keywords_json"] or "[]")),
        quiet_hours_start=row["quiet_hours_start"],
        quiet_hours_end=row["quiet_hours_end"],
    )


def reward_from_row(row: Any) -> VerizonShineReward:
    return VerizonShineReward(
        guild_id=int(row["guild_id"]),
        reward_id=str(row["reward_id"]),
        title=str(row["title"]),
        reward_type=str(row["reward_type"]),
        status=str(row["status"]),
        source=str(row["source"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        available_at=row["available_at"],
        expires_at=row["expires_at"],
        priority=str(row["priority"]),
        raw_text=str(row["raw_text"] or ""),
        fingerprint_hash=str(row["fingerprint_hash"]),
    )


def reward_from_due_row(row: dict[str, Any]) -> VerizonShineReward:
    return VerizonShineReward(
        guild_id=int(row["guild_id"]),
        reward_id=str(row["reward_id"]),
        title=str(row["title"]),
        reward_type=str(row["reward_type"]),
        status=str(row["status"]),
        source=str(row["source"]),
        first_seen_at=str(row["first_seen_at"]),
        last_seen_at=str(row["last_seen_at"]),
        available_at=row.get("available_at"),
        expires_at=row.get("expires_at"),
        priority=str(row.get("priority") or "normal"),
        raw_text=str(row.get("raw_text") or ""),
        fingerprint_hash=str(row.get("fingerprint_hash") or ""),
    )


def status_label(status: str) -> str:
    labels = {
        "available": "Available now",
        "coming_soon": "Coming soon",
        "sold_out": "Sold out / claimed",
        "expired": "Expired",
        "unknown": "Unknown",
    }
    return labels.get(status, status.replace("_", " ").title())


def human_time(value: str | None) -> str:
    parsed = parse_iso_datetime(value)
    if not parsed:
        return "Not detected"
    return parsed.astimezone(ET).strftime("%a %b %d, %I:%M %p ET")


def build_summary_lines(reward: VerizonShineReward) -> list[str]:
    lines = [
        f"Type: **{reward.reward_type}**",
        f"Status: **{status_label(reward.status)}**",
        f"Priority: **{reward.priority.title()}**",
    ]
    if reward.available_at:
        lines.append(f"Available: **{human_time(reward.available_at)}**")
    if reward.expires_at:
        lines.append(f"Expires: **{human_time(reward.expires_at)}**")
    lines.append("Action: open **My Verizon app → Me → Shine**.")
    return lines


async def sleep_until(target: datetime) -> None:
    delay = max(0.0, (target - utc_now()).total_seconds())
    await asyncio.sleep(delay)
