from __future__ import annotations

import asyncio

import discord

from sniperplug.cogs import auto_scan_runner as legacy
from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.services.walmart_catalog_coverage import (
    CatalogCoverageSlice,
    rotating_catalog_slice,
)
from sniperplug.services.walmart_exact_public_lane import normalize_exact_verified_walmart_cards


NATIVE_MANUAL_QUERY_COUNT = 8
NATIVE_MANUAL_TIMEOUT_SECONDS = 90
NATIVE_BROAD_PRESET_KEY = "catalog_wide_rotating"


class AutoScanRunnerCog(legacy.AutoScanRunnerCog):
    """Single-pass autoscan runner with persistent catalog-wide rotation.

    A single keyword pass cannot enumerate Walmart's entire live catalog. Each
    run takes the next bounded slice from one deduplicated route pool spanning
    every configured category, broad department, markdown surface, condition
    route, and Walmart Cash route. Discovered item IDs persist in the global
    exact-detail queue, while only exact-verified deals can reach users.
    """

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
                            report_label="Manual catalog pass" if force else "Manual pass",
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
            settings = {"forced": True, "retailer": legacy.AUTO_SCAN_RETAILER, "coverage": "catalog_wide_rotating"}

        query_count = resolve_native_query_count(force=force, query_count_override=query_count_override)
        coverage = rotating_catalog_slice(guild_id=guild.guild_id, query_count=query_count)
        preset = build_native_broad_preset(None, guild_id=guild.guild_id, query_count=query_count, coverage=coverage)
        result = await legacy.run_autoscan_verified_category(self.bot.db, guild.guild_id, preset=preset)
        warnings = legacy.clean_warning_list(result.warnings)
        warnings.append(coverage.summary_line())
        warnings.append(
            "Walmart Cash routes are included for discovery. Cash never substitutes for trusted markdown proof; "
            "when Walmart returns strict API Cash proof, the exact dollar amount is attached to the verified deal card."
        )
        warnings.append(
            f"Verified-only result policy: a deal requires **{result.min_discount}%+** trusted Walmart markdown proof. "
            "Anything uncertain is suppressed and never shown as a deal."
        )
        if report_label:
            warnings.append(f"{report_label}: scanned **{len(preset.queries)}** rotating catalog route(s) in **{preset.label}**.")

        diagnostics = legacy.autoscan_diagnostics(result)
        review = getattr(result, "review_candidates", None)
        suppressed_unverified_count = len(getattr(review, "cards", ()) or ())
        if suppressed_unverified_count:
            warnings.append(
                f"Suppressed **{suppressed_unverified_count}** unverified candidate(s); none were displayed or posted."
            )

        unique_cards = legacy.dedupe_cards(result.cards)
        unique_cards = legacy.rank_for_search_mode(
            unique_cards,
            [],
            legacy.AUTO_SCAN_PUBLIC_MODE,
            limit=max(len(unique_cards), legacy.AUTO_SCAN_PUBLIC_LIMIT),
        ).verified

        normalized_exact_markdowns = normalize_exact_verified_walmart_cards(
            unique_cards,
            min_discount=result.min_discount,
        )
        if normalized_exact_markdowns:
            warnings.append(
                f"Refreshed **{normalized_exact_markdowns}** exact Walmart card(s) for idempotent public gating; "
                "auxiliary promos remain attached without demoting the verified markdown."
            )

        unique_cards = await legacy.apply_feedback_learning_to_cards(
            self.bot.db,
            guild_id=guild.guild_id,
            cards=unique_cards,
            fallback_retailer=legacy.AUTO_SCAN_RETAILER,
        )
        feedback_summary = legacy.summarize_feedback_learning(unique_cards)

        proof_ready_cards = legacy.select_public_deal_candidates(
            unique_cards,
            source_label=f"{legacy.AUTO_SCAN_SOURCE_LABEL}:{preset.key}",
            min_discount=result.min_discount,
            limit=max(len(unique_cards), legacy.AUTO_SCAN_PUBLIC_LIMIT),
        )
        quality_blocked_count = max(0, len(unique_cards) - len(proof_ready_cards))
        if quality_blocked_count:
            warnings.append(
                f"Public-proof gate blocked **{quality_blocked_count}** exact card(s) before confidence ranking. Confidence-ready now means proof-ready too."
            )

        confidence_selection = legacy.select_confident_public_cards(
            proof_ready_cards,
            floor=legacy.AUTOSCAN_CONFIDENCE_FLOOR,
        )
        public_candidates = list(confidence_selection.cards)

        # prepare_public_deal_candidate adds a public-proof explanation field.
        # Refresh exact cards before the next quality pass so words describing
        # rejected scout signals cannot make a proven card reject itself.
        normalize_exact_verified_walmart_cards(
            public_candidates,
            min_discount=result.min_discount,
        )

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
        if not shown_cards:
            public_result = legacy.PublicPostResult()
            report = legacy.AutoScanReport(
                guild_id=guild.guild_id,
                allowed=True,
                reason="Auto-scan completed with no new/lower-price exact-verified Walmart deals that met the threshold.",
                settings=settings,
                category_key=preset.key,
                category_label=preset.label,
                min_discount=result.min_discount,
                public_mode="Exact-Verified Deals Only",
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
                total_cards=len(proof_ready_cards),
                verified_before_memory=result.total_verified_cards,
                fresh_cards=0,
                cards_attempted_for_public=0,
                used_repeat_fallback=False,
                repeat_summary=(
                    f"{fresh_selection.summary_line()} • public-proof blocked: **{quality_blocked_count}** • "
                    f"unverified candidates suppressed: **{suppressed_unverified_count}** • unverified cards shown: **0**"
                ),
                public_result=public_result,
                warnings=tuple(warnings),
            )
            await legacy.persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
            if not force:
                await legacy.record_auto_scan_run(self.bot.db, guild.guild_id, legacy.AUTO_SCAN_RETAILER, scan_key=scan_key)
            legacy.log.info("Auto-scan completed with verified-only output %s", report.log_fields())
            return report

        category_preferences = await legacy.get_category_preferences(self.bot.db, guild.guild_id)
        shown_cards, category_suppressed_cards, category_notes = legacy.apply_category_preferences(shown_cards, category_preferences)
        if category_suppressed_cards:
            warnings.append(f"Category preferences suppressed **{len(category_suppressed_cards)}** normal deal(s).")
        warnings.extend(note for note in category_notes[:4] if note not in warnings)

        # The public sender performs one final proof check. Remove the previous
        # gate's explanatory field and guarantee Walmart Cash amount rendering
        # immediately before that final check.
        normalize_exact_verified_walmart_cards(
            shown_cards,
            min_discount=result.min_discount,
        )

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
            public_mode="Exact-Verified Deals Only",
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
            total_cards=len(proof_ready_cards),
            verified_before_memory=result.total_verified_cards,
            fresh_cards=len(fresh_selection.fresh),
            cards_attempted_for_public=len(shown_cards),
            used_repeat_fallback=False,
            repeat_summary=(
                legacy.watchlist_repeat_summary(fresh_selection.summary_line(), [], public_result)
                + f" • public-proof blocked: **{quality_blocked_count}**"
                + f" • unverified candidates suppressed: **{suppressed_unverified_count}** • unverified cards shown: **0**"
            ),
            public_result=public_result,
            warnings=tuple(warnings),
        )
        await legacy.persist_autoscan_report(self.bot.db, report, scan_key=scan_key)
        if not force:
            await legacy.record_auto_scan_run(self.bot.db, guild.guild_id, legacy.AUTO_SCAN_RETAILER, scan_key=scan_key)
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
        summary = report.discord_summary()
        summary = summary.replace(
            "Setup note: green setup means SniperPlug can post; the finder still needs verified Walmart markdown proof before a deal is public-ready. Scout/review leads stay private.",
            "Result policy: only exact-verified Walmart deals are shown. Unverified candidates are suppressed.",
        )
        summary = summary.replace("Private review fallback found: **yes**", "Unverified cards shown: **0**")
        summary = summary.replace("Private review fallback found: **no**", "Unverified cards shown: **0**")

        embed = discord.Embed(
            title=f"🎯 {label}",
            description=summary[:4000],
            color=discord.Color.green() if report.public_result.posted else discord.Color.orange(),
        )
        if report.decision_trail_summary:
            embed.add_field(
                name="Verified-deal decision trail",
                value=legacy.trim_discord_value(report.decision_trail_summary),
                inline=False,
            )
        if report.route_summary:
            embed.add_field(
                name="Top search routes",
                value=legacy.trim_discord_value(report.route_summary),
                inline=False,
            )
        if report.warnings:
            warning_text = chr(10).join(f"• {warning}" for warning in report.warnings[:5])
            embed.add_field(name="Verification notes", value=warning_text, inline=False)
        if not report.public_result.posted:
            embed.add_field(
                name="Why no verified deal was shown",
                value=legacy.trim_discord_value(legacy.autoscan_blocker_summary(report)),
                inline=False,
            )
        if report.public_result.errors:
            error_text = chr(10).join(f"• {legacy.clean_log_text(error)}" for error in report.public_result.errors[:5])
            embed.add_field(name="Errors", value=error_text, inline=False)
        embed.set_footer(text="Exact-verified deals only. Search hints and review-only candidates are never displayed as deals.")
        await self._safe_autoscan_followup(interaction, embed=embed)


def resolve_native_query_count(*, force: bool, query_count_override: int | None) -> int:
    return max(
        1,
        int(
            query_count_override
            if query_count_override is not None
            else NATIVE_MANUAL_QUERY_COUNT if force else legacy.AUTO_SCAN_SCHEDULED_QUERY_COUNT
        ),
    )


def select_native_autoscan_preset(
    guild_id: int,
    *,
    force: bool = False,
    query_count_override: int | None = None,
) -> HuntPreset:
    query_count = resolve_native_query_count(force=force, query_count_override=query_count_override)
    coverage = rotating_catalog_slice(guild_id=guild_id, query_count=query_count)
    return build_native_broad_preset(None, guild_id=guild_id, query_count=query_count, coverage=coverage)


def build_native_broad_preset(
    presets: dict[str, HuntPreset] | None,
    *,
    guild_id: int,
    query_count: int,
    coverage: CatalogCoverageSlice | None = None,
) -> HuntPreset:
    del presets  # Compatibility parameter retained for older callers/tests.
    selected = coverage or rotating_catalog_slice(guild_id=guild_id, query_count=query_count)
    return HuntPreset(
        NATIVE_BROAD_PRESET_KEY,
        "Catalog-Wide Rotating Sweep",
        "🌐",
        "Rotating discovery across all configured Walmart categories, broad departments, markdown surfaces, conditions, and Walmart Cash routes.",
        selected.queries,
        50,
    )
