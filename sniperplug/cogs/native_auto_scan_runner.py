from __future__ import annotations

import asyncio

import discord

from sniperplug.cogs import auto_scan_runner as legacy
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.manual_review_share import ManualReviewShareView


NATIVE_MANUAL_QUERY_COUNT = 6
NATIVE_REVIEW_CARD_LIMIT = 12
NATIVE_REVIEW_PAGE_SIZE = 3
NATIVE_MANUAL_TIMEOUT_SECONDS = 90
NATIVE_PUBLIC_SCOUT_LIMIT = 2


class AutoScanRunnerCog(legacy.AutoScanRunnerCog):
    """Single-pass autoscan runner.

    The manual command does one bounded pass, posts verified public cards when
    available, then falls back to conservative public scout posts and private
    review cards from the same result object.
    """

    def __init__(self, bot):
        super().__init__(bot)
        self._review_cards_by_guild: dict[int, tuple[legacy.DealCard, ...]] = {}

    async def _run_autoscan_now_background(self, interaction: discord.Interaction, guild_id: int, force: bool) -> None:
        lock = legacy.autoscan_lock(guild_id)
        async with lock:
            try:
                target_channel = interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
                repair = await legacy.repair_public_alert_setup(self.bot.db, self.bot, guild_id, target_channel=target_channel)
                config = repair.config if repair.config is not None else await legacy.get_public_alert_config(self.bot.db, guild_id)
                if repair.human_action_required:
                    await self._safe_autoscan_followup(interaction, "SniperPlug could not safely repair posting setup. " + repair.discord_line())
                    return
                if not config.get("enabled") or not config.get("channel_id"):
                    await self._safe_autoscan_followup(interaction, "Public alerts are missing. Run `/autoscan_health` for the exact blocker.")
                    return
                if legacy.AUTO_SCAN_RETAILER not in set(config.get("retailers") or ()): 
                    await self._safe_autoscan_followup(interaction, "Walmart is not enabled for public alerts in this server. Run `/autoscan_health`.")
                    return

                progress_task = asyncio.create_task(self._autoscan_progress_notice(interaction))
                try:
                    report = await asyncio.wait_for(
                        self._run_guild_walmart_discovery(
                            legacy.AutoScanGuild(guild_id, config.get("channel_id")),
                            force=force,
                            query_count_override=NATIVE_MANUAL_QUERY_COUNT,
                            report_label="Manual pass",
                        ),
                        timeout=NATIVE_MANUAL_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    await self._safe_autoscan_followup(interaction, "Auto-scan took too long, so I stopped this manual pass before it could stall the bot.")
                    return
                finally:
                    progress_task.cancel()
                await self._send_autoscan_report(interaction, report, label="Manual pass result")
            except Exception as exc:
                legacy.log.exception("Manual /autoscan_now failed guild=%s", guild_id)
                await self._safe_autoscan_followup(interaction, f"Auto-scan hit an error after starting: `{legacy.clean_log_text(exc)}`")

    async def _run_guild_walmart_discovery(self, guild: legacy.AutoScanGuild, *, force: bool = False, query_count_override: int | None = None, report_label: str = "") -> legacy.AutoScanReport:
        scan_key = legacy.AUTO_SCAN_SOURCE_LABEL
        settings: dict = {}
        if not force:
            allowed, reason, settings = await legacy.auto_scan_allowed(self.bot.db, guild.guild_id, legacy.AUTO_SCAN_RETAILER, scan_key=scan_key)
            if not allowed:
                report = legacy.AutoScanReport(guild_id=guild.guild_id, allowed=False, reason=reason, settings=settings)
                await legacy.persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
                return report
        else:
            settings = {"forced": True, "retailer": legacy.AUTO_SCAN_RETAILER}

        preset = legacy.select_autoscan_preset(guild.guild_id, force=force, query_count_override=query_count_override)
        result = await legacy.run_autoscan_verified_category(self.bot.db, guild.guild_id, preset=preset)
        warnings = legacy.clean_warning_list(result.warnings)
        if report_label:
            warnings.append(f"{report_label}: scanned **{len(preset.queries)}** route(s) in this pass.")
        diagnostics = legacy.autoscan_diagnostics(result)

        unique_cards = legacy.dedupe_cards(result.cards)
        unique_cards = legacy.rank_for_search_mode(unique_cards, [], legacy.AUTO_SCAN_PUBLIC_MODE, limit=max(len(unique_cards), legacy.AUTO_SCAN_PUBLIC_LIMIT)).verified
        unique_cards = await legacy.apply_feedback_learning_to_cards(self.bot.db, guild_id=guild.guild_id, cards=unique_cards, fallback_retailer=legacy.AUTO_SCAN_RETAILER)
        feedback_summary = legacy.summarize_feedback_learning(unique_cards)
        confidence_selection = legacy.select_confident_public_cards(unique_cards, floor=legacy.AUTOSCAN_CONFIDENCE_FLOOR)
        public_candidates = list(confidence_selection.cards)
        rescued_cards = legacy.select_public_deal_candidates(unique_cards, source_label=f"{legacy.AUTO_SCAN_SOURCE_LABEL}:{preset.key}", min_discount=result.min_discount, limit=legacy.AUTO_SCAN_PUBLIC_LIMIT)
        for card in rescued_cards:
            if card not in public_candidates:
                public_candidates.append(card)

        fresh_selection = await legacy.select_fresh_deal_cards(
            self.bot.db,
            guild_id=guild.guild_id,
            cards=public_candidates,
            fallback_retailer=legacy.AUTO_SCAN_RETAILER,
            limit=legacy.AUTO_SCAN_PUBLIC_LIMIT,
            hide_active_cache_repeats=False,
            min_public_discount=result.min_discount,
            source_label=f"{legacy.AUTO_SCAN_SOURCE_LABEL}:{preset.key}",
        )
        decision_trail_summary = legacy.explain_autoscan_decision_trail(
            all_verified_cards=unique_cards,
            confidence_cards=list(confidence_selection.cards),
            public_candidates=public_candidates,
            fresh_cards=list(fresh_selection.fresh),
            min_discount=result.min_discount,
            confidence_floor=legacy.AUTOSCAN_CONFIDENCE_FLOOR,
            limit=8,
        )

        shown_cards = list(fresh_selection.fresh)
        review_cards = legacy.prepare_review_watchlist_cards(result, limit=NATIVE_REVIEW_CARD_LIMIT) if not shown_cards else []
        if review_cards:
            self._review_cards_by_guild[int(guild.guild_id)] = tuple(review_cards[:NATIVE_REVIEW_CARD_LIMIT])
            warnings.append("Private review cards were captured from this same autoscan pass.")
        else:
            self._review_cards_by_guild.pop(int(guild.guild_id), None)

        if not force:
            await legacy.record_auto_scan_run(self.bot.db, guild.guild_id, legacy.AUTO_SCAN_RETAILER, scan_key=scan_key)

        if not shown_cards:
            scout_public_result = legacy.PublicPostResult()
            if review_cards:
                scout_public_result = await legacy.maybe_post_public_deal_cards(
                    bot=self.bot,
                    guild_id=guild.guild_id,
                    cards=review_cards[:NATIVE_PUBLIC_SCOUT_LIMIT],
                    source_label=f"{legacy.AUTO_SCAN_SOURCE_LABEL}:{preset.key}:public_scout",
                    fallback_retailer=legacy.AUTO_SCAN_RETAILER,
                    min_public_discount=result.min_discount,
                    allow_review_scout=True,
                )
                if scout_public_result.posted:
                    warnings.append(f"Public Scout Lane posted **{scout_public_result.posted}** high-confidence review lead(s).")
                elif scout_public_result.attempted:
                    warnings.append("Public Scout Lane checked review leads but did not find one safe enough to auto-post.")

            report = legacy.AutoScanReport(
                guild_id=guild.guild_id,
                allowed=True,
                reason="Auto-scan completed with no new/lower-price API-verified public deals that met the threshold.",
                settings=settings,
                category_key=preset.key,
                category_label=preset.label,
                min_discount=result.min_discount,
                public_mode="Verified API Threshold + Public Scout Fallback",
                confidence_floor=legacy.AUTOSCAN_CONFIDENCE_FLOOR,
                confidence_summary=confidence_selection.summary_line(),
                feedback_learning_summary=feedback_summary,
                verification_failure_summary=diagnostics["verification_failure_summary"],
                review_candidate_summary=diagnostics["review_candidate_summary"],
                decision_trail_summary=decision_trail_summary,
                route_summary=diagnostics["route_summary"],
                price_memory_summary=diagnostics["price_memory_summary"],
                products_checked=result.products_checked,
                searches_checked=result.searches_attempted,
                total_cards=len(unique_cards),
                verified_before_memory=result.total_verified_cards,
                fresh_cards=0,
                cards_attempted_for_public=scout_public_result.attempted,
                used_repeat_fallback=bool(review_cards),
                repeat_summary=f"{fresh_selection.summary_line()} • public scout attempted: **{scout_public_result.attempted}** • private review/scout cards ready: **{len(review_cards)}**",
                public_result=scout_public_result,
                warnings=tuple(warnings),
            )
            await legacy.persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
            legacy.log.info("Auto-scan completed with public scout fallback %s", report.log_fields())
            return report

        category_preferences = await legacy.get_category_preferences(self.bot.db, guild.guild_id)
        shown_cards, category_suppressed_cards, category_notes = legacy.apply_category_preferences(shown_cards, category_preferences)
        if category_suppressed_cards:
            warnings.append(f"Category preferences suppressed **{len(category_suppressed_cards)}** normal lead(s).")
        warnings.extend(note for note in category_notes[:4] if note not in warnings)

        public_result = await legacy.maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=guild.guild_id,
            cards=shown_cards,
            source_label=f"{legacy.AUTO_SCAN_SOURCE_LABEL}:{preset.key}",
            fallback_retailer=legacy.AUTO_SCAN_RETAILER,
            min_public_discount=result.min_discount,
        )
        report = legacy.AutoScanReport(
            guild_id=guild.guild_id,
            allowed=True,
            settings=settings,
            category_key=preset.key,
            category_label=preset.label,
            min_discount=result.min_discount,
            public_mode="Best Picks",
            confidence_floor=legacy.AUTOSCAN_CONFIDENCE_FLOOR,
            confidence_summary=confidence_selection.summary_line(),
            feedback_learning_summary=feedback_summary,
            verification_failure_summary=diagnostics["verification_failure_summary"],
            review_candidate_summary=diagnostics["review_candidate_summary"],
            decision_trail_summary=decision_trail_summary,
            route_summary=diagnostics["route_summary"],
            price_memory_summary=diagnostics["price_memory_summary"],
            products_checked=result.products_checked,
            searches_checked=result.searches_attempted,
            total_cards=len(unique_cards),
            verified_before_memory=result.total_verified_cards,
            fresh_cards=len(fresh_selection.fresh),
            cards_attempted_for_public=len(shown_cards),
            used_repeat_fallback=bool(review_cards),
            repeat_summary=legacy.watchlist_repeat_summary(fresh_selection.summary_line(), review_cards, public_result),
            public_result=public_result,
            warnings=tuple(warnings),
        )
        await legacy.persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
        legacy.log.info("Auto-scan completed %s reason=%s", report.log_fields(), legacy.compact_log_text(legacy.explain_public_post_result(public_result)))
        return report

    async def _send_autoscan_report(self, interaction: discord.Interaction, report: legacy.AutoScanReport, *, label: str = "Auto-scan test result") -> None:
        await super()._send_autoscan_report(interaction, report, label=label)
        if not report.allowed or report.public_result.posted:
            return
        cards = list(self._review_cards_by_guild.pop(int(report.guild_id), ()))[:NATIVE_REVIEW_CARD_LIMIT]
        if not cards:
            await self._safe_autoscan_followup(interaction, "🟨 No reusable private review cards were produced from this pass.")
            return
        for index, card in enumerate(cards, start=1):
            annotate_private_review_card(card, index=index)
        await self._send_private_review_cards(interaction, cards)

    async def _send_private_review_cards(self, interaction: discord.Interaction, cards: list[legacy.DealCard]) -> None:
        view = ManualReviewShareView(cards, page_size=NATIVE_REVIEW_PAGE_SIZE, max_cards=NATIVE_REVIEW_CARD_LIMIT)
        content = view.content(prefix="🟨 **Private autoscan review leads**\nThese are from the same autoscan pass. They need staff verification before public posting.")
        try:
            await interaction.followup.send(content=content, embeds=view.page_embeds(), view=view, ephemeral=True)
        except (discord.NotFound, discord.HTTPException) as exc:
            if legacy.interaction_token_is_gone(exc):
                if await self._send_autoscan_dm_fallback(interaction, content=content, embed=sanitize_embed(cards[0].embed)):
                    return
            legacy.log.exception("Failed to send autoscan private review leads")


def annotate_private_review_card(card: legacy.DealCard, *, index: int) -> None:
    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return
    if any(str(field.name or "") == "🟨 Private autoscan lead" for field in embed.fields):
        return
    embed.add_field(
        name="🟨 Private autoscan lead",
        value=f"Lead #{index}. Same-pass private review card. Use Post only after checking price, seller, exact variant, reviews, and comps.",
        inline=False,
    )
