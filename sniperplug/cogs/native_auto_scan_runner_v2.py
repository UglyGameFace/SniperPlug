from __future__ import annotations

import discord

from sniperplug.cogs import auto_scan_runner as legacy
from sniperplug.cogs.native_auto_scan_runner import (
    AutoScanRunnerCog as BaseAutoScanRunnerCog,
    NATIVE_BROAD_PRESET_KEY,
    NATIVE_REVIEW_CARD_LIMIT,
    annotate_private_review_card,
    select_native_autoscan_preset,
)
from sniperplug.services.autoscan_route_policy import PUBLIC_AUTOSCAN_ROUTE_POLICY_NOTE


class AutoScanRunnerCog(BaseAutoScanRunnerCog):
    """Corrected native autoscan runner.

    Verified public deals and private staff-review leads are independent outputs
    from the same scan. A successful public post must never discard or hide
    uncertain review cards.
    """

    async def _run_guild_walmart_discovery(
        self,
        guild: legacy.AutoScanGuild,
        *,
        force: bool = False,
        query_count_override: int | None = None,
        report_label: str = "",
    ) -> legacy.AutoScanReport:
        scan_key = legacy.AUTO_SCAN_SOURCE_LABEL
        settings: dict = {}
        if not force:
            allowed, reason, settings = await legacy.auto_scan_allowed(
                self.bot.db,
                guild.guild_id,
                legacy.AUTO_SCAN_RETAILER,
                scan_key=scan_key,
            )
            if not allowed:
                report = legacy.AutoScanReport(guild_id=guild.guild_id, allowed=False, reason=reason, settings=settings)
                await legacy.persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
                return report
        else:
            settings = {"forced": True, "retailer": legacy.AUTO_SCAN_RETAILER, "coverage": "broad_public_safe"}

        preset = select_native_autoscan_preset(guild.guild_id, force=force, query_count_override=query_count_override)
        result = await legacy.run_autoscan_verified_category(self.bot.db, guild.guild_id, preset=preset)
        warnings = legacy.clean_warning_list(result.warnings)
        warnings.append(PUBLIC_AUTOSCAN_ROUTE_POLICY_NOTE)
        warnings.append(
            f"Verified-only public policy: auto-posting requires **{result.min_discount}%+** trusted Walmart markdown proof. "
            "Anything uncertain remains private for staff review."
        )
        if preset.key == NATIVE_BROAD_PRESET_KEY:
            warnings.append("Manual broad sweep spans the major public-safe categories instead of staying inside one category.")
        if report_label:
            warnings.append(f"{report_label}: scanned **{len(preset.queries)}** public-safe route(s) in **{preset.label}**.")

        diagnostics = legacy.autoscan_diagnostics(result)
        unique_cards = legacy.dedupe_cards(result.cards)
        unique_cards = legacy.rank_for_search_mode(
            unique_cards,
            [],
            legacy.AUTO_SCAN_PUBLIC_MODE,
            limit=max(len(unique_cards), legacy.AUTO_SCAN_PUBLIC_LIMIT),
        ).verified
        unique_cards = await legacy.apply_feedback_learning_to_cards(
            self.bot.db,
            guild_id=guild.guild_id,
            cards=unique_cards,
            fallback_retailer=legacy.AUTO_SCAN_RETAILER,
        )
        feedback_summary = legacy.summarize_feedback_learning(unique_cards)
        confidence_selection = legacy.select_confident_public_cards(unique_cards, floor=legacy.AUTOSCAN_CONFIDENCE_FLOOR)
        public_candidates = list(confidence_selection.cards)
        for card in legacy.select_public_deal_candidates(
            unique_cards,
            source_label=f"{legacy.AUTO_SCAN_SOURCE_LABEL}:{preset.key}",
            min_discount=result.min_discount,
            limit=legacy.AUTO_SCAN_PUBLIC_LIMIT,
        ):
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
        review_cards = legacy.prepare_review_watchlist_cards(result, limit=NATIVE_REVIEW_CARD_LIMIT)
        if review_cards:
            self._review_cards_by_guild[int(guild.guild_id)] = tuple(review_cards[:NATIVE_REVIEW_CARD_LIMIT])
            warnings.append(
                f"Captured **{len(review_cards[:NATIVE_REVIEW_CARD_LIMIT])}** private review lead(s) from this same pass; none were auto-posted."
            )
        else:
            self._review_cards_by_guild.pop(int(guild.guild_id), None)

        if not force:
            await legacy.record_auto_scan_run(self.bot.db, guild.guild_id, legacy.AUTO_SCAN_RETAILER, scan_key=scan_key)

        if not shown_cards:
            public_result = legacy.PublicPostResult()
            report = legacy.AutoScanReport(
                guild_id=guild.guild_id,
                allowed=True,
                reason="Auto-scan completed with no new/lower-price API-verified public deals that met the threshold.",
                settings=settings,
                category_key=preset.key,
                category_label=preset.label,
                min_discount=result.min_discount,
                public_mode="Verified API Threshold Only",
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
                cards_attempted_for_public=0,
                used_repeat_fallback=bool(review_cards),
                repeat_summary=f"{fresh_selection.summary_line()} • private review cards ready: **{len(review_cards)}** • public review posts: **0**",
                public_result=public_result,
                warnings=tuple(warnings),
            )
            await legacy.persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
            legacy.log.info("Auto-scan completed with private review fallback %s", report.log_fields())
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
            public_mode="Verified API Threshold Only",
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
            repeat_summary=(
                legacy.watchlist_repeat_summary(fresh_selection.summary_line(), [], public_result)
                + f" • private review cards ready: **{len(review_cards)}**"
            ),
            public_result=public_result,
            warnings=tuple(warnings),
        )
        await legacy.persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
        legacy.log.info(
            "Auto-scan completed %s reason=%s",
            report.log_fields(),
            legacy.compact_log_text(legacy.explain_public_post_result(public_result)),
        )
        return report

    async def _send_autoscan_report(
        self,
        interaction: discord.Interaction,
        report: legacy.AutoScanReport,
        *,
        label: str = "Auto-scan test result",
    ) -> None:
        await super()._send_autoscan_report(interaction, report, label=label)
        if not report.allowed:
            return
        cards = list(self._review_cards_by_guild.pop(int(report.guild_id), ()))[:NATIVE_REVIEW_CARD_LIMIT]
        if not cards:
            if not report.public_result.posted:
                await self._safe_autoscan_followup(interaction, "🟨 No reusable private review cards were produced from this pass.")
            return
        for index, card in enumerate(cards, start=1):
            annotate_private_review_card(card, index=index)
        await self._send_private_review_cards(interaction, cards)
