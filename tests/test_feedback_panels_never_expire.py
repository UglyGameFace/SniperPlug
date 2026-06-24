from pathlib import Path


FEEDBACK = Path("sniperplug/services/deal_feedback.py").read_text(encoding="utf-8")
BOT = Path("sniperplug/bot.py").read_text(encoding="utf-8")


def test_feedback_views_are_timeout_none_even_without_token():
    assert "super().__init__(timeout=None)" in FEEDBACK
    assert "timeout=None if persistent else 86400" not in FEEDBACK


def test_legacy_feedback_buttons_have_catch_all_persistent_view():
    assert "DealFeedbackView(None, persistent=True)" in FEEDBACK
    assert "feedback_target_from_interaction_message" in FEEDBACK
    assert "I caught this old feedback button" in FEEDBACK


def test_feedback_callback_defers_before_database_work():
    callback_start = FEEDBACK.index("async def callback(self, interaction: discord.Interaction)")
    callback_end = FEEDBACK.index("async def build_deal_feedback_view", callback_start)
    body = FEEDBACK[callback_start:callback_end]
    assert "await interaction.response.defer(ephemeral=True)" in body
    assert body.index("await interaction.response.defer") < body.index("await record_deal_feedback")


def test_bot_registers_persistent_feedback_views_on_startup():
    assert "register_persistent_feedback_views" in BOT
    assert "Persistent deal feedback views registered" in BOT
