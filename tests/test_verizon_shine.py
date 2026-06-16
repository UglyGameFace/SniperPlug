from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from sniperplug.services.verizon_shine import (
    VerizonShineConfig,
    VerizonShineReward,
    parse_rewards_from_text,
    should_alert,
)


class VerizonShineParserTests(unittest.TestCase):
    def test_parses_daily_drop_gift_card_with_relative_timer(self) -> None:
        now = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
        rewards = parse_rewards_from_text(
            123,
            "Verizon Shine Daily Drop\n$25 gift card\nAvailable in 30 minutes",
            now=now,
        )

        self.assertEqual(len(rewards), 1)
        reward = rewards[0]
        self.assertEqual(reward.reward_type, "Daily Drop")
        self.assertEqual(reward.status, "coming_soon")
        self.assertEqual(reward.priority, "high")
        self.assertEqual(datetime.fromisoformat(reward.available_at or ""), now + timedelta(minutes=30))

    def test_duplicate_suppression_and_status_change(self) -> None:
        now = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
        config = VerizonShineConfig(guild_id=123)

        incoming = parse_rewards_from_text(
            123,
            "Verizon Shine Presale\nTicket access starts in 10 minutes",
            now=now,
        )[0]
        existing = VerizonShineReward(**incoming.__dict__)

        alert, reason = should_alert(existing, incoming, config, now=now)
        self.assertFalse(alert)
        self.assertEqual(reason, "duplicate suppressed")

        changed = VerizonShineReward(**incoming.__dict__)
        changed.status = "available"

        alert, reason = should_alert(existing, changed, config, now=now)
        self.assertTrue(alert)
        self.assertIn("status changed", reason)


if __name__ == "__main__":
    unittest.main()
