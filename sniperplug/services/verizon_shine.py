from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, Iterable


class FixedOffsetTimezone(tzinfo):
    def __init__(self, offset_hours: int, name: str) -> None:
        self._offset = timedelta(hours=offset_hours)
        self._name = name

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self._offset

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return self._name


def eastern_tz() -> tzinfo:
    """Return America/New_York when tzdata exists, otherwise a safe ET fallback.

    Termux and tiny containers may not have the IANA tzdata package installed.
    Importing this module must not crash the bot just because local dev lacks
    tzdata. The fallback is intentionally only used for parsing text hints; live
    scheduling still stores UTC timestamps.
    """
    try:
        return ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        return FixedOffsetTimezone(-5, "ET")


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
    et = eastern_tz()

    iso_match = re.search(r"(\d{4}-\d{2}-\d{2})(?:[ t](\d{1,2}:\d{2})(?::\d{2})?)?", haystack)
    if iso_match:
        date_part = iso_match.group(1)
        time_part = iso_match.group(2) or "00:00"
        try:
            dt = datetime.fromisoformat(f"{date_part}T{time_part}")
            return dt.replace(tzinfo=et).astimezone(UTC)
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

    now_et = now.astimezone(et)
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

    target = datetime.combine(target_date, time(hour, minute), tzinfo=et)
    if target <= now_et and "today" not in lower and "tomorrow" not in lower:
        target += timedelta(days=1)
    return target.astimezone(UTC)


def split_reward_blocks(text: str) -> list[str]:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return []
    blocks = [part.strip() for part in re.split(r"\n\s*\n|^-{3,}$", cleaned, flags=re.MULTILINE) if part.strip()]
    if len(blocks) == 1:
        blocks = [part.strip(" -•\t") for part in re.split(r"(?:^|\n)\s*(?:[-•]|\d+[.)])\s+", cleaned) if part.strip(" -•\t")]
    return blocks


def parse_rewards_from_text(text: str, *, guild_id: int, source: str, keywords: Iterable[str] = DEFAULT_PRIORITY_KEYWORDS) -> list[VerizonShineReward]:
    rewards: list[VerizonShineReward] = []
    now = utc_now_iso()
    for block in split_reward_blocks(text):
        title = _title_from_block(block)
        if not title:
            continue
        status = classify_status(block)
        available_at = parse_datetime_hint(block)
        reward = VerizonShineReward(
            guild_id=guild_id,
            reward_id=reward_id_for(guild_id, title, source),
            title=title,
            reward_type=classify_type(block),
            status=status,
            source=source,
            first_seen_at=now,
            last_seen_at=now,
            available_at=available_at.isoformat() if available_at else None,
            priority=priority_for(block, keywords),
            raw_text=block,
            fingerprint_hash=fingerprint(title, status, source),
        )
        rewards.append(reward)
    return rewards


def _title_from_block(block: str) -> str:
    lines = [line.strip(" -•\t") for line in block.splitlines() if line.strip(" -•\t")]
    if not lines:
        return ""
    for line in lines:
        if not re.search(r"\b(status|source|available|expires|starts|opens)\b\s*:", line, flags=re.IGNORECASE):
            return line[:160]
    return lines[0][:160]


async def upsert_reward(db: Any, reward: VerizonShineReward) -> tuple[bool, VerizonShineReward]:
    existing = await get_reward(db, reward.guild_id, reward.reward_id)
    conn = db.require_conn()
    if existing is None:
        await conn.execute(
            """
            INSERT INTO verizon_shine_rewards (
                guild_id, reward_id, title, reward_type, status, source,
                first_seen_at, last_seen_at, available_at, expires_at, priority, raw_text, fingerprint_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        return True, reward

    await conn.execute(
        """
        UPDATE verizon_shine_rewards
        SET title = ?, reward_type = ?, status = ?, source = ?, last_seen_at = ?,
            available_at = COALESCE(?, available_at), expires_at = COALESCE(?, expires_at),
            priority = ?, raw_text = ?, fingerprint_hash = ?
        WHERE guild_id = ? AND reward_id = ?
        """,
        (
            reward.title,
            reward.reward_type,
            reward.status,
            reward.source,
            reward.last_seen_at,
            reward.available_at,
            reward.expires_at,
            reward.priority,
            reward.raw_text,
            reward.fingerprint_hash,
            reward.guild_id,
            reward.reward_id,
        ),
    )
    await conn.commit()
    return False, reward


async def get_reward(db: Any, guild_id: int, reward_id: str) -> VerizonShineReward | None:
    conn = db.require_conn()
    cursor = await conn.execute("SELECT * FROM verizon_shine_rewards WHERE guild_id = ? AND reward_id = ?", (guild_id, reward_id))
    row = await cursor.fetchone()
    if not row:
        return None
    return reward_from_row(row)


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
        fingerprint_hash=str(row["fingerprint_hash"] or ""),
    )
