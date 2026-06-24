from pathlib import Path
import re

DEALS = Path("sniperplug/cogs/deal_scanner.py").read_text(encoding="utf-8")


def _method_source(src: str, name: str) -> str:
    marker = f"async def {name}("
    start = src.index(marker)
    search_from = start + len(marker)

    candidates = []
    for token in (
        "\n    @app_commands.",
        "\n    @commands.",
        "\n    async def ",
        "\n    def ",
    ):
        pos = src.find(token, search_from)
        if pos != -1:
            candidates.append(pos)

    end = min(candidates) if candidates else len(src)
    return src[start:end]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


COMMAND = _method_source(DEALS, "walmart_cash")
HELPER = _method_source(DEALS, "_send_walmart_cash_search")
COMMAND_C = _compact(COMMAND)
HELPER_C = _compact(HELPER)


TRUTH = Path("sniperplug/services/walmart_cash_api_truth.py").read_text(encoding="utf-8")
PROVIDER = Path("sniperplug/providers/walmart.py").read_text(encoding="utf-8")


def test_cash_truth_blocks_generic_rewards_and_onepay():
    lower = TRUTH.lower()
    assert "walmartcash" in lower
    assert "onepay" in lower
    assert "generic" in lower
    assert "return none" in lower


def test_provider_preserves_raw_api_payload_for_cash_proof():
    lower = PROVIDER.lower()
    assert "raw_api" in lower or "raw_item" in lower or "api_item" in lower


def test_walmart_cash_command_uses_fast_helper_settings():
    assert "queries[:2]" in HELPER_C
    assert "timeout=8" in HELPER_C
    assert "asyncio.Semaphore(2)" in HELPER_C
