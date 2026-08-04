from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services import fresh_deal_filter as fresh_filter
from sniperplug.services.autoscan_decision_trail import explain_autoscan_decision_trail
from sniperplug.services.deal_finder_telemetry import SearchRouteStats, top_route_lines
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
        return _Cursor((455, 0, 0, 0, 310, 290, 120, 0, 25, 10, 5))


class _Database:
    def require_conn(self):
        return _Connection()


def test_queue_health_separates_transient_retries_from_identity_blocks() -> None:
    health = asyncio.run(load_walmart_exact_queue_health(_Database()))

    assert health.total == 455
    assert health.due_now == 0
    assert health.initial_due_now == 0
    assert health.recheck_due_now == 0
    assert health.delayed_retries == 310
    assert health.identity_blocked == 290
    assert health.stale == 5
    assert "due now **0**" in health.summary_line()
    assert "new/retry **0**" in health.summary_line()
    assert "scheduled rechecks **0**" in health.summary_line()
    assert "delayed transient retries **310**" in health.summary_line()
    assert "identity unavailable / safely blocked **290**" in health.summary_line()
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
    assert second.autoscan_preflight_reason == "not selected: public post cap kept the top 1 ranked deal(s)"


class _FreshCursor:
    async def fetchone(self):
        return None


class _FreshConnection:
    async def execute(self, _sql, _params=()):
        return _FreshCursor()


class _FreshDatabase:
    def require_conn(self):
        return _FreshConnection()


def test_guild_preflight_annotates_cards_after_public_cap(monkeypatch) -> None:
    first = SimpleNamespace(retailer="walmart", current_price=10.0, url="https://www.walmart.com/ip/1")
    second = SimpleNamespace(retailer="walmart", current_price=11.0, url="https://www.walmart.com/ip/2")

    async def no_op(*_args, **_kwargs):
        return None

    async def no_recent(*_args, **_kwargs):
        return None

    monkeypatch.setattr(fresh_filter, "ensure_public_post_tables", no_op)
    monkeypatch.setattr(fresh_filter, "safe_find_recent_alert", no_recent)
    monkeypatch.setattr(
        fresh_filter,
        "prepare_public_deal_candidate",
        lambda card, **kwargs: True,
    )
    monkeypatch.setattr(fresh_filter, "card_product_key", lambda card, retailer: card.url)
    monkeypatch.setattr(fresh_filter, "card_deal_key", lambda card, retailer: card.url)

    selection = asyncio.run(
        select_fresh_deal_cards(
            _FreshDatabase(),
            guild_id=123,
            cards=[first, second],
            limit=1,
            hide_active_cache_repeats=False,
            source_label="test",
        )
    )

    assert selection.fresh == [first]
    assert "passed quality and public duplicate preflight" in first.autoscan_preflight_reason
    assert second.autoscan_preflight_reason == "not selected: public post cap kept the top 1 ranked deal(s)"


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


def test_decision_trail_never_calls_bounded_selection_unidentified() -> None:
    card = DealCard(
        embed=discord.Embed(title="Ranked sixth"),
        url="https://www.walmart.com/ip/456",
        label="Ranked sixth",
        score=165,
        discount=68,
    )
    card.current_price = 1.28

    text = explain_autoscan_decision_trail(
        all_verified_cards=[card],
        confidence_cards=[card],
        public_candidates=[card],
        fresh_cards=[],
        min_discount=50,
        confidence_floor=78,
    )

    assert "not selected by the bounded public preflight" in text
    assert "unidentified preflight gate" not in text


def test_route_summary_separates_api_notes_from_errors() -> None:
    note_only = SearchRouteStats(
        query="toy clearance",
        pages_checked=2,
        returned_products=49,
        warnings=("WALMART_PUBLISHER_ID is blank; using direct Walmart links.",),
    )
    actual_error = SearchRouteStats(
        query="electronics clearance",
        pages_checked=2,
        returned_products=50,
        warnings=("Walmart API HTTP 500: temporary failure",),
    )

    lines = top_route_lines([note_only, actual_error], limit=2)

    assert "1 API note(s)" in lines[0]
    assert "warning(s)" not in lines[0]
    assert "1 error(s)" in lines[1]


def test_runner_logs_phase_and_full_queue_health() -> None:
    assert "phase=%s" in RUNNER
    assert "load_walmart_exact_queue_health" in RUNNER
    assert "metadata_snapshot_nodes=2500" in RUNNER
    assert 'self._runtime_phase = "exact_verification_queue"' in RUNNER
