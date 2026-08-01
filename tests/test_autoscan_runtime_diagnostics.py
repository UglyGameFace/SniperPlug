from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services import fresh_deal_filter as fresh_filter
from sniperplug.services.autoscan_decision_trail import explain_autoscan_decision_trail
from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.walmart_exact_queue_health import (
    load_walmart_exact_queue_health,
)
from sniperplug.services.walmart_metadata_snapshot_guard import (
    MAX_SNAPSHOT_NODES,
    bounded_snapshot_payload,
)


REPO = Path(__file__).resolve().parents[1]
RUNNER = (REPO / "sniperplug/cogs/resilient_auto_scan_runner.py").read_text(
    encoding="utf-8"
)


def test_bounded_metadata_snapshot_caps_oversized_payload() -> None:
    payload = {
        "fulfillmentOptions": [
            {"type": "SHIPPING", "status": "IN_STOCK", "arrivalText": "Tomorrow"}
        ],
        "hugeRecommendations": [
            {"nested": {"value": index, "more": [index] * 20}}
            for index in range(10_000)
        ],
    }

    snapshot = bounded_snapshot_payload(payload)

    assert len(snapshot.containers) + len(snapshot.leaves) <= MAX_SNAPSHOT_NODES
    assert any("fulfillmentOptions" in path for path, _ in snapshot.containers)


class _Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _Connection:
    async def execute(self, _sql, _params=()):
        return _Cursor((455, 0, 310, 290, 120, 0, 25, 10, 5))


class _Database:
    def require_conn(self):
        return _Connection()


def test_queue_health_distinguishes_due_zero_from_unfinished_rows() -> None:
    health = asyncio.run(load_walmart_exact_queue_health(_Database()))

    assert health.total == 455
    assert health.due_now == 0
    assert health.delayed_retries == 310
    assert health.identity_blocked == 290
    assert health.stale == 5
    assert "due now **0**" in health.summary_line()
    assert "identity blocked **290**" in health.summary_line()
    assert "stale/unclaimable **5**" in health.summary_line()


def test_fresh_filter_does_not_promote_score_only_card_without_exact_proof() -> None:
    card = DealCard(
        embed=discord.Embed(title="Missing exact price"),
        url="https://www.walmart.com/ip/123",
        label="Missing exact price",
        score=100,
        discount=70,
    )

    selection = asyncio.run(
        select_fresh_deal_cards(
            None,
            guild_id=None,
            cards=[card],
            min_public_discount=50,
            source_label="test",
        )
    )

    assert selection.fresh == []
    assert selection.not_alertable == 1
    assert "missing a numeric exact current price" in card.autoscan_preflight_reason


def test_no_guild_preflight_labels_quality_card_outside_limit(monkeypatch) -> None:
    first = SimpleNamespace()
    second = SimpleNamespace()
    monkeypatch.setattr(
        fresh_filter,
        "prepare_public_deal_candidate",
        lambda card, **kwargs: True,
    )

    selection = asyncio.run(
        select_fresh_deal_cards(
            None,
            guild_id=None,
            cards=[first, second],
            limit=1,
            min_public_discount=50,
            source_label="test",
        )
    )

    assert selection.fresh == [first]
    assert first.autoscan_preflight_reason == "passed public quality preflight"
    assert second.autoscan_preflight_reason == "not selected: outside top 1 public-quality result(s)"


def test_decision_trail_uses_concrete_preflight_reason() -> None:
    card = DealCard(
        embed=discord.Embed(title="Duplicate deal"),
        url="https://www.walmart.com/ip/123",
        label="Duplicate deal",
        score=100,
        discount=70,
    )
    card.current_price = 10.0
    card.autoscan_preflight_reason = "this exact deal fingerprint was already posted"

    text = explain_autoscan_decision_trail(
        all_verified_cards=[card],
        confidence_cards=[card],
        public_candidates=[card],
        fresh_cards=[],
        min_discount=50,
        confidence_floor=78,
    )

    assert "exact deal fingerprint was already posted" in text
    assert "fresh/duplicate/public-post preflight" not in text


def test_runner_logs_phase_and_full_queue_health() -> None:
    assert "phase=%s" in RUNNER
    assert "load_walmart_exact_queue_health" in RUNNER
    assert "metadata_snapshot_nodes=2500" in RUNNER
    assert 'self._runtime_phase = "exact_verification_queue"' in RUNNER
