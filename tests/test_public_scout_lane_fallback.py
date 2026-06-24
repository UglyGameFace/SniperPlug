from pathlib import Path


QUALITY = Path("sniperplug/services/public_deal_quality.py").read_text(encoding="utf-8")
POSTS = Path("sniperplug/services/public_deal_posts.py").read_text(encoding="utf-8")
AUTO = Path("sniperplug/cogs/auto_scan_runner.py").read_text(encoding="utf-8")
DB = Path("sniperplug/storage/db.py").read_text(encoding="utf-8")
FEEDBACK = Path("sniperplug/services/deal_feedback.py").read_text(encoding="utf-8")


def test_scout_lane_exists_and_is_clearly_labeled():
    assert "PUBLIC_SCOUT_LANE_FIELD" in QUALITY
    assert "prepare_public_scout_candidate" in QUALITY
    assert "Verify app price, selected option, seller, shipping, stock, and comps" in QUALITY


def test_public_posting_allows_scout_lane_without_verified_cache_pollution():
    assert "allow_review_scout: bool = False" in POSTS
    assert "prepare_public_scout_candidate" in POSTS
    assert "if not allow_review_scout:" in POSTS
    assert "cache_after_posting.append(card)" in POSTS


def test_autoscan_posts_scout_when_verified_lane_empty():
    assert "Public Scout Lane only posts high-confidence leads" in AUTO
    assert "allow_review_scout=True" in AUTO
    assert "Auto-scan posted public Scout Lane lead" in AUTO


def test_libsql_fetchall_uses_same_connection_lock():
    assert "def __init__(self, result: Any, lock: asyncio.Lock | None = None)" in DB
    assert "async with self._lock:" in DB
    assert "_LibsqlAsyncCursor(result, self._lock)" in DB


def test_feedback_defers_before_database_work():
    callback_start = FEEDBACK.index("async def callback(self, interaction: discord.Interaction)")
    callback_end = FEEDBACK.index("async def safe_feedback_reply") if "async def safe_feedback_reply" in FEEDBACK[callback_start:] else callback_start + 1800
    body = FEEDBACK[callback_start:callback_end]
    assert "await interaction.response.defer(ephemeral=True)" in body
    assert body.index("await interaction.response.defer") < body.index("db = getattr")
    assert "await interaction.response.send_message(result.message" not in body
