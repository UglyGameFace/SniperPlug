from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord.ext import commands, tasks

from sniperplug.services.movie_ticket_drops import ATOM_PROMOTIONS_URL, clean_text
from sniperplug.services.movie_ticket_feedback import (
    FAILED_RESULT,
    WORKED_RESULT,
    MovieTicketFeedbackContext,
    MovieTicketFeedbackCounts,
    MovieTicketFeedbackResult,
    MovieTicketFeedbackStore,
)


log = logging.getLogger("sniperplug.movie_ticket_feedback")

MOVIE_WORKED_ID = "movies:ticket-feedback:worked"
MOVIE_FAILED_ID = "movies:ticket-feedback:failed"
RECONCILE_SECONDS = 15
RECONCILE_LIMIT = 100
MOVIE_TICKET_ALERT_TITLES = frozenset(
    {
        "🎟️ FREE ATOM TICKET DROP",
        "🎟️ FREE FANDANGO TICKET OFFER",
    }
)


class MovieTicketFeedbackView(discord.ui.View):
    """Restart-safe community redemption controls for a public ticket alert."""

    def __init__(
        self,
        cog: "MovieTicketFeedbackCog",
        *,
        offer_url: str = ATOM_PROMOTIONS_URL,
        counts: MovieTicketFeedbackCounts | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        current = counts or MovieTicketFeedbackCounts()
        self.worked_button.label = f"Worked ({current.worked})"
        self.failed_button.label = f"Didn't work ({current.failed})"
        normalized_offer_url = offer_url or ATOM_PROMOTIONS_URL
        offer_label = (
            "Open official Fandango offer"
            if "fandango" in normalized_offer_url.lower()
            else "Open official Atom offer"
        )
        self.add_item(
            discord.ui.Button(
                label=offer_label,
                style=discord.ButtonStyle.link,
                url=normalized_offer_url,
                emoji="🎟️",
                row=1,
            )
        )

    @discord.ui.button(
        label="Worked (0)",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id=MOVIE_WORKED_ID,
        row=0,
    )
    async def worked_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.record_feedback(interaction, WORKED_RESULT)

    @discord.ui.button(
        label="Didn't work (0)",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id=MOVIE_FAILED_ID,
        row=0,
    )
    async def failed_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.record_feedback(interaction, FAILED_RESULT)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        log.error(
            "Movie ticket feedback component failed item=%s guild=%s user=%s",
            getattr(item, "custom_id", type(item).__name__),
            interaction.guild_id,
            interaction.user.id if interaction.user else None,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_feedback_error(interaction, error)


class MovieTicketFeedbackCog(commands.Cog):
    """Attach and process community worked/did-not-work results."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = MovieTicketFeedbackStore(bot.db)
        self._applied_counts: dict[int, tuple[int, int, str]] = {}
        self._message_tasks: set[asyncio.Task[None]] = set()

    async def cog_load(self) -> None:
        await self.store.ensure_schema()
        self.bot.add_view(MovieTicketFeedbackView(self))
        if not self.feedback_reconcile.is_running():
            self.feedback_reconcile.start()
        log.info("Persistent movie ticket feedback controls registered")

    async def cog_unload(self) -> None:
        if self.feedback_reconcile.is_running():
            self.feedback_reconcile.cancel()
        for task in tuple(self._message_tasks):
            task.cancel()
        self._message_tasks.clear()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Upgrade a newly posted movie alert without waiting for reconciliation."""

        if not self.bot.user or message.author.id != self.bot.user.id:
            return
        if not message.guild or not message.embeds:
            return
        if message.embeds[0].title not in MOVIE_TICKET_ALERT_TITLES:
            return

        task = asyncio.create_task(self._attach_new_message_after_delivery_commit(message))
        self._message_tasks.add(task)
        task.add_done_callback(self._message_tasks.discard)

    async def _attach_new_message_after_delivery_commit(self, message: discord.Message) -> None:
        # The message event can arrive just before MovieTicketsCog commits its
        # delivery row. Retry briefly, then let the reconciliation loop handle it.
        for _ in range(8):
            context = await self.store.resolve_message(
                guild_id=message.guild.id,
                message_id=message.id,
            )
            if context is not None:
                await self._apply_view_to_message(message, context)
                return
            await asyncio.sleep(0.5)

    async def record_feedback(self, interaction: discord.Interaction, result: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            if not interaction.guild_id or not interaction.user or not interaction.message:
                await interaction.followup.send(
                    "This feedback button must be used on a server movie-ticket alert.",
                    ephemeral=True,
                )
                return

            context = await self.store.resolve_message(
                guild_id=int(interaction.guild_id),
                message_id=int(interaction.message.id),
            )
            if context is None:
                await interaction.followup.send(
                    "I couldn't match this message to a saved ticket drop. The alert may be too old or from a previous test build.",
                    ephemeral=True,
                )
                return

            feedback = await self.store.record_vote(
                guild_id=context.guild_id,
                drop_id=context.drop_id,
                user_id=int(interaction.user.id),
                result=result,
            )
            await interaction.message.edit(
                view=MovieTicketFeedbackView(
                    self,
                    offer_url=context.offer_url,
                    counts=feedback.counts,
                )
            )
            self._applied_counts[context.message_id] = (
                feedback.counts.worked,
                feedback.counts.failed,
                context.offer_url,
            )
            await interaction.followup.send(
                feedback_confirmation(feedback),
                ephemeral=True,
            )
        except Exception as error:  # noqa: BLE001 - component must always answer visibly.
            log.exception(
                "Movie ticket feedback failed guild=%s user=%s message=%s",
                interaction.guild_id,
                interaction.user.id if interaction.user else None,
                interaction.message.id if interaction.message else None,
            )
            await send_feedback_error(interaction, error)

    @tasks.loop(seconds=RECONCILE_SECONDS)
    async def feedback_reconcile(self) -> None:
        try:
            deliveries = await self.store.list_recent_deliveries(limit=RECONCILE_LIMIT)
            for context in deliveries:
                counts = await self.store.get_counts(
                    guild_id=context.guild_id,
                    drop_id=context.drop_id,
                )
                signature = (counts.worked, counts.failed, context.offer_url)
                if self._applied_counts.get(context.message_id) == signature:
                    continue
                message = await self._fetch_delivery_message(context)
                if message is None:
                    continue
                await self._apply_view_to_message(message, context, counts=counts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Movie ticket feedback reconciliation failed safely")

    @feedback_reconcile.before_loop
    async def before_feedback_reconcile(self) -> None:
        await self.bot.wait_until_ready()

    async def _apply_view_to_message(
        self,
        message: discord.Message,
        context: MovieTicketFeedbackContext,
        *,
        counts: MovieTicketFeedbackCounts | None = None,
    ) -> None:
        current = counts or await self.store.get_counts(
            guild_id=context.guild_id,
            drop_id=context.drop_id,
        )
        await message.edit(
            view=MovieTicketFeedbackView(
                self,
                offer_url=context.offer_url,
                counts=current,
            )
        )
        self._applied_counts[context.message_id] = (
            current.worked,
            current.failed,
            context.offer_url,
        )

    async def _fetch_delivery_message(
        self,
        context: MovieTicketFeedbackContext,
    ) -> discord.Message | None:
        guild = self.bot.get_guild(context.guild_id)
        if guild is None:
            return None
        channel = guild.get_channel(context.channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(context.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if not isinstance(channel, discord.TextChannel):
            return None
        try:
            return await channel.fetch_message(context.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None


def feedback_confirmation(feedback: MovieTicketFeedbackResult) -> str:
    selected = "✅ **Worked for me**" if feedback.current_result == WORKED_RESULT else "❌ **Didn't work for me**"
    if feedback.repeated:
        action = f"You already selected {selected}."
    elif feedback.changed:
        action = f"Your report was changed to {selected}."
    else:
        action = f"Thanks — recorded {selected}."

    counts = feedback.counts
    return (
        f"{action}\n"
        f"Community results: **{counts.worked} worked** • **{counts.failed} didn't work**.\n"
        "Reports help other users, but they do not automatically disable an official code because theater, account, date, and inventory restrictions can differ."
    )


async def send_feedback_error(interaction: discord.Interaction, error: Exception) -> None:
    message = (
        "I couldn't save that ticket result right now. Please try once more. "
        f"Error: `{clean_text(str(error))[:300] or type(error).__name__}`"
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        log.exception("Could not send movie ticket feedback fallback response")
