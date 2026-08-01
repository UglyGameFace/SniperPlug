from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "sniperplug/bot.py").read_text(encoding="utf-8")
COG = (ROOT / "sniperplug/cogs/movie_ticket_feedback.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "sniperplug/services/movie_ticket_feedback.py").read_text(encoding="utf-8")


def test_runtime_registers_exactly_one_movie_ticket_feedback_cog() -> None:
    assert "from sniperplug.cogs.movie_ticket_feedback import MovieTicketFeedbackCog" in BOT
    assert BOT.count("await self.add_cog(MovieTicketFeedbackCog(self))") == 1
    assert BOT.index("await self.add_cog(MovieTicketsCog(self))") < BOT.index(
        "await self.add_cog(MovieTicketFeedbackCog(self))"
    )


def test_feedback_controls_are_restart_safe_and_visible_on_new_and_old_alerts() -> None:
    assert 'MOVIE_WORKED_ID = "movies:ticket-feedback:worked"' in COG
    assert 'MOVIE_FAILED_ID = "movies:ticket-feedback:failed"' in COG
    assert "self.bot.add_view(MovieTicketFeedbackView(self))" in COG
    assert "@commands.Cog.listener()" in COG
    assert "async def on_message" in COG
    assert "@tasks.loop(seconds=RECONCILE_SECONDS)" in COG
    assert "list_recent_deliveries" in COG
    assert 'message.embeds[0].title != "🎟️ FREE ATOM TICKET DROP"' in COG


def test_feedback_is_one_vote_per_user_and_cannot_auto_kill_codes() -> None:
    assert "PRIMARY KEY (guild_id, drop_id, user_id)" in SERVICE
    assert "ON CONFLICT(guild_id, drop_id, user_id) DO UPDATE" in SERVICE
    assert "GROUP BY result" in SERVICE
    assert "UPDATE movie_ticket_drops SET active" not in SERVICE
    assert "do not automatically disable" in COG


def test_feedback_resolves_only_real_saved_delivery_messages() -> None:
    assert "JOIN movie_ticket_drops AS drop_row" in SERVICE
    assert "delivery.message_id = ?" in SERVICE
    assert "delivery.state = 'sent'" in SERVICE
    assert "interaction.message.id" in COG
