from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from sniperplug.services.movie_ticket_artwork import normalize_public_code
from sniperplug.services.movie_ticket_drops import ATOM_PROMOTIONS_URL, MovieTicketDrop, clean_text
from sniperplug.services.movie_ticket_feedback import (
    MovieTicketFeedbackTarget,
    build_movie_ticket_feedback_view,
    ensure_movie_ticket_feedback_table,
    list_recent_movie_ticket_deliveries,
    register_persistent_movie_ticket_feedback_views,
)


log = logging.getLogger("sniperplug.movie_ticket_feedback")

MOVIE_DROP_EMBED_TITLE = "🎟️ FREE ATOM TICKET DROP"
FEEDBACK_UPGRADE_DELAY_SECONDS = 0.15


class MovieTicketFeedbackCog(commands.Cog):
    """Attach persistent community redemption feedback to movie-ticket alerts."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._upgrade_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        await ensure_movie_ticket_feedback_table(self.bot.db)
        registered = await register_persistent_movie_ticket_feedback_views(self.bot)
        self._upgrade_task = asyncio.create_task(
            self._upgrade_existing_alerts(),
            name="movie-ticket-feedback-upgrade",
        )
        log.info("Movie ticket feedback loaded persistent_views=%s", registered)

    async def cog_unload(self) -> None:
        task = self._upgrade_task
        self._upgrade_task = None
        if task and not task.done():
            task.cancel()

    @commands.Cog.listener("on_message")
    async def attach_feedback_to_new_drop(self, message: discord.Message) -> None:
        if not self.bot.user or message.author.id != self.bot.user.id:
            return
        if message.guild is None or not message.embeds:
            return
        embed = message.embeds[0]
        if clean_text(embed.title) != MOVIE_DROP_EMBED_TITLE:
            return

        target = await self._target_from_embed(embed)
        if target is None:
            log.warning("Could not match movie drop for feedback message=%s", message.id)
            return
        try:
            drop = await self._load_drop(target.drop_id)
            if drop is None:
                return
            view = await build_movie_ticket_feedback_view(self.bot, drop)
            await message.edit(view=view)
            log.info(
                "Attached movie feedback buttons guild=%s channel=%s message=%s drop=%s",
                message.guild.id,
                message.channel.id,
                message.id,
                target.drop_id,
            )
        except (discord.NotFound, discord.Forbidden):
            return
        except discord.HTTPException:
            log.exception("Discord rejected movie feedback attachment message=%s", message.id)
        except Exception:
            log.exception("Movie feedback attachment failed message=%s", message.id)

    async def _upgrade_existing_alerts(self) -> None:
        try:
            await self.bot.wait_until_ready()
            deliveries = await list_recent_movie_ticket_deliveries(self.bot.db)
            upgraded = 0
            for delivery in deliveries:
                try:
                    channel = self.bot.get_channel(delivery.channel_id)
                    if channel is None:
                        channel = await self.bot.fetch_channel(delivery.channel_id)
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    message = await channel.fetch_message(delivery.message_id)
                    drop = await self._load_drop(delivery.drop_id)
                    if drop is None:
                        continue
                    view = await build_movie_ticket_feedback_view(self.bot, drop)
                    await message.edit(view=view)
                    upgraded += 1
                    await asyncio.sleep(FEEDBACK_UPGRADE_DELAY_SECONDS)
                except asyncio.CancelledError:
                    raise
                except (discord.NotFound, discord.Forbidden):
                    continue
                except discord.HTTPException:
                    log.warning(
                        "Could not upgrade movie feedback message guild=%s channel=%s message=%s",
                        delivery.guild_id,
                        delivery.channel_id,
                        delivery.message_id,
                    )
                except Exception:
                    log.exception("Unexpected movie feedback upgrade failure message=%s", delivery.message_id)
            log.info("Movie feedback upgraded existing alerts=%s checked=%s", upgraded, len(deliveries))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Movie feedback existing-alert upgrade failed safely")

    async def _target_from_embed(self, embed: discord.Embed) -> MovieTicketFeedbackTarget | None:
        offer_url = clean_text(embed.url)
        promo_code = ""
        for field in embed.fields:
            if clean_text(field.name).lower().startswith("promo code"):
                promo_code = normalize_public_code(str(field.value or ""))
                break

        conn = self.bot.db.require_conn()
        if offer_url and promo_code:
            cursor = await conn.execute(
                """
                SELECT drop_id, offer_url
                FROM movie_ticket_drops
                WHERE offer_url = ? AND UPPER(code) = ?
                ORDER BY active DESC, last_seen_at DESC
                LIMIT 1
                """,
                (offer_url, promo_code),
            )
            row = await cursor.fetchone()
            if row:
                return MovieTicketFeedbackTarget(
                    drop_id=clean_text(_row_value(row, "drop_id")),
                    offer_url=clean_text(_row_value(row, "offer_url")) or ATOM_PROMOTIONS_URL,
                )

        if promo_code:
            cursor = await conn.execute(
                """
                SELECT drop_id, offer_url
                FROM movie_ticket_drops
                WHERE UPPER(code) = ?
                ORDER BY active DESC, last_seen_at DESC
                LIMIT 1
                """,
                (promo_code,),
            )
            row = await cursor.fetchone()
            if row:
                return MovieTicketFeedbackTarget(
                    drop_id=clean_text(_row_value(row, "drop_id")),
                    offer_url=clean_text(_row_value(row, "offer_url")) or ATOM_PROMOTIONS_URL,
                )
        return None

    async def _load_drop(self, drop_id: str) -> MovieTicketDrop | None:
        conn = self.bot.db.require_conn()
        cursor = await conn.execute(
            """
            SELECT drop_id, source_key, source_label, title, code, classification,
                   ticket_limit, offer_url, validity_text, restrictions_json,
                   raw_text, first_seen_at, last_seen_at, active
            FROM movie_ticket_drops
            WHERE drop_id = ?
            LIMIT 1
            """,
            (clean_text(drop_id),),
        )
        row = await cursor.fetchone()
        if not row:
            return None

        import json

        try:
            restrictions_data = json.loads(str(_row_value(row, "restrictions_json") or "[]"))
        except (TypeError, ValueError):
            restrictions_data = []
        restrictions = tuple(clean_text(item) for item in restrictions_data if clean_text(item))
        return MovieTicketDrop(
            drop_id=clean_text(_row_value(row, "drop_id")),
            source_key=clean_text(_row_value(row, "source_key")),
            source_label=clean_text(_row_value(row, "source_label")),
            title=clean_text(_row_value(row, "title")),
            code=normalize_public_code(str(_row_value(row, "code") or "")),
            classification=clean_text(_row_value(row, "classification")),
            ticket_limit=max(1, int(_row_value(row, "ticket_limit") or 1)),
            offer_url=clean_text(_row_value(row, "offer_url")) or ATOM_PROMOTIONS_URL,
            validity_text=clean_text(_row_value(row, "validity_text")),
            restrictions=restrictions,
            raw_text=str(_row_value(row, "raw_text") or ""),
            first_seen_at=clean_text(_row_value(row, "first_seen_at")),
            last_seen_at=clean_text(_row_value(row, "last_seen_at")),
            active=bool(_row_value(row, "active")),
        )


def _row_value(row: object, key: str) -> object:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)
