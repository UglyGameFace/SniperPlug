from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from sniperplug.services import walmart_delivery_health as health


@dataclass
class FakeCursor:
    rows: list[dict]

    async def fetchall(self):
        return list(self.rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    async def execute(self, sql: str, params=()):
        if "FROM walmart_global_exact_deal_events" in sql and "COUNT(*)" not in sql:
            return FakeCursor(
                [
                    {
                        "deal_key": "posted",
                        "snapshot_json": "posted",
                        "first_seen_at": "2026-08-04T20:00:00+00:00",
                        "source_verified_at": "2026-08-04T20:00:00+00:00",
                        "processed_at": "2026-08-04T20:01:00+00:00",
                        "last_error": "",
                    },
                    {
                        "deal_key": "below",
                        "snapshot_json": "below",
                        "first_seen_at": "2026-08-04T19:00:00+00:00",
                        "source_verified_at": "2026-08-04T19:00:00+00:00",
                        "processed_at": "2026-08-04T19:01:00+00:00",
                        "last_error": "",
                    },
                    {
                        "deal_key": "muted",
                        "snapshot_json": "muted",
                        "first_seen_at": "2026-08-04T18:00:00+00:00",
                        "source_verified_at": "2026-08-04T18:00:00+00:00",
                        "processed_at": "2026-08-04T18:01:00+00:00",
                        "last_error": "",
                    },
                    {
                        "deal_key": "quality",
                        "snapshot_json": "quality",
                        "first_seen_at": "2026-08-04T17:00:00+00:00",
                        "source_verified_at": "2026-08-04T17:00:00+00:00",
                        "processed_at": "2026-08-04T17:01:00+00:00",
                        "last_error": "",
                    },
                    {
                        "deal_key": "eligible",
                        "snapshot_json": "eligible",
                        "first_seen_at": "2026-08-04T16:00:00+00:00",
                        "source_verified_at": "2026-08-04T16:00:00+00:00",
                        "processed_at": "2026-08-04T16:01:00+00:00",
                        "last_error": "",
                    },
                    {
                        "deal_key": "pending",
                        "snapshot_json": "pending",
                        "first_seen_at": "2026-08-04T15:00:00+00:00",
                        "source_verified_at": "2026-08-04T15:00:00+00:00",
                        "processed_at": None,
                        "last_error": "",
                    },
                ]
            )
        if "COUNT(*) AS total" in sql:
            return FakeCursor([{"total": 42, "pending": 3}])
        if "FROM guild_public_deal_posts" in sql:
            return FakeCursor(
                [
                    {
                        "deal_key": "posted",
                        "status": "posted",
                        "first_seen_at": "2026-08-04T20:00:00+00:00",
                        "posted_at": "2026-08-04T20:01:00+00:00",
                    }
                ]
            )
        raise AssertionError(sql)


class FakeDatabase:
    def require_conn(self):
        return FakeConnection()


def _card(snapshot: str):
    discounts = {
        "posted": 60.0,
        "below": 25.0,
        "muted": 80.0,
        "quality": 75.0,
        "eligible": 70.0,
        "pending": 55.0,
    }
    return SimpleNamespace(
        label=f"Card {snapshot}",
        retailer="walmart",
        public_post_key=snapshot,
        discount=discounts[snapshot],
    )


def test_walmart_delivery_health_classifies_current_rules(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(health, "_candidate_from_snapshot", lambda value: value)
        monkeypatch.setattr(health.legacy, "_exact_card_for_candidate", _card)
        monkeypatch.setattr(
            health,
            "structured_discount",
            lambda card: card.discount,
        )
        monkeypatch.setattr(
            health,
            "decide_category",
            lambda card, _preferences: SimpleNamespace(
                action="suppress" if card.public_post_key == "muted" else "allow",
                category_label="Muted test category",
            ),
        )
        monkeypatch.setattr(
            health,
            "is_public_deal_candidate",
            lambda card, **_kwargs: card.public_post_key != "quality",
        )

        result = await health.load_walmart_delivery_health(
            FakeDatabase(),
            guild_id=1098088221457514609,
            threshold=40,
            category_preferences={},
        )

        assert result.events_seen == 6
        assert result.events_processed == 5
        assert result.events_pending == 1
        assert result.posted == 1
        assert result.below_threshold == 1
        assert result.category_muted == 1
        assert result.quality_blocked == 1
        assert result.eligible_without_post == 1
        assert result.total_event_rows == 42
        assert result.total_pending_rows == 3
        assert result.has_delivery_problem is True
        assert "below **40%** **1**" in result.summary_line(threshold=40)
        assert "eligible events without a durable post receipt" in result.summary_line(
            threshold=40
        )

    asyncio.run(run())


def test_zero_recent_events_is_not_reported_as_a_broken_scanner(monkeypatch) -> None:
    class EmptyConnection(FakeConnection):
        async def execute(self, sql: str, params=()):
            if "COUNT(*) AS total" in sql:
                return FakeCursor([{"total": 12, "pending": 0}])
            return FakeCursor([])

    class EmptyDatabase:
        def require_conn(self):
            return EmptyConnection()

    async def run() -> None:
        result = await health.load_walmart_delivery_health(
            EmptyDatabase(),
            guild_id=1098088221457514609,
            threshold=40,
            category_preferences={},
        )
        summary = result.summary_line(threshold=40)
        assert result.events_seen == 0
        assert result.has_delivery_problem is False
        assert "Nothing new reached the current **40%+** server gate" in summary

    asyncio.run(run())
