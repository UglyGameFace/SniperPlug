# Active Task

## Status
**Active — SP-DEALS-DISCOVER-HARDENING-244 live follow-up. PR #245 merged, main CI passed, and the merged build was deployed. The second live `/deals` test improved initial response time but still failed the button gate: some mode controls returned Discord `SniperPlug didn't respond in time`, and the result presentation remained too dense. Focused follow-up implementation is in progress on `fix/deals-live-timeout-245`.**

## Scope
Finish the existing `/deals` + `/discover` hardening task. Do not start the suspended Walmart missed-deals backlog until this live gate passes.

## Current live findings
- Merged commit `84db8b9` deployed successfully and startup was healthy.
- `/deals` now renders without the previous 10+ minute wait, so the bounded interactive-search work helped.
- Mode controls are still not reliable: Hidden Gems and additional mode buttons can produce Discord `SniperPlug didn't respond in time`.
- The current View creates another control message after every mode switch while reusing the same static custom IDs across all menus; this creates unnecessary competing View instances and a stale-dispatch surface.
- Button acknowledgement is split across individual callbacks instead of one earliest View-level path; failed acknowledgement currently returns silently with no task-specific diagnostic.
- The user-facing summary/menu and Walmart proof cards expose too much internal API/audit detail for the normal `/deals` shopping flow even though the price data itself is useful.

## Focused follow-up implementation
- Give each `/deals` result menu a unique token in every component custom ID.
- Acknowledge every component in `DealSearchModeView.interaction_check` using the component-native deferred message update before any button-specific work.
- Add task-specific acknowledgement and callback-error logs instead of swallowing component failures.
- Keep one control-panel message: mode changes edit the existing menu instead of spawning duplicate menus.
- Make the selected mode visually obvious; only the selected mode is primary/disabled.
- Keep Fresh Scan bounded and reuse the same menu token/owner across refreshes.
- Add `/deals`-only compact result embeds that preserve price, seller, delivery, links, verified/private status and thumbnails while omitting internal identity/evidence noise. Autoscan/public proof rendering is unchanged.
- Compress the normal finder summary and controls menu; deep diagnostics remain available in diagnostic/health surfaces rather than the beginner command.

## Validation required
1. Focused control/UX regressions pass.
2. Existing PR #244/#245 deal/discover regressions pass.
3. `pip check`, compileall, and diff/static validation pass.
4. Full Python 3.11 test suite passes.
5. Final branch conflict/cleanup inspection shows no temporary workflow or duplicate implementation.
6. PR checks pass, review/comments/threads are clean, squash merge, and post-merge `main` checks pass.
7. Deploy merged main to Discloud app `1779293887444`.
8. Live `/deals search:phone mount`: initial result is responsive; every enabled mode button responds; Fresh Scan responds; one control panel remains current; compact embeds are readable.
9. Live `/discover` still returns only current-job item IDs as established by PR #244.
10. Only then close SP-DEALS-DISCOVER-HARDENING-244.

## Suspended task
**SP-WM-MISSED-DEALS-236 remains suspended, not closed.** Resume only after this active task closes or an explicit FORCE SWITCH.

## Backlog
- Resume Walmart exact-verification/page-recovery throughput work after #244 closes.
- Investigate remaining Discord gateway lag if it persists after this command path is stable.
- Resume SniperPlug-Site Whop content-reader work later.

<!-- DEALS_PATCH_START -->
from pathlib import Path

path = Path('sniperplug/cogs/unified_deal_scanner.py')
text = path.read_text()


def replace_once(old: str, new: str) -> None:
    global text
    if old not in text:
        raise SystemExit(f'missing phase1 target: {old[:100]!r}')
    text = text.replace(old, new, 1)


replace_once(
    'from __future__ import annotations\n\nimport discord\n',
    'from __future__ import annotations\n\nimport logging\n\nimport discord\n',
)
replace_once(
    'DEAL_MODE_VIEW_TIMEOUT_SECONDS = 15 * 60\n\n\nclass UnifiedDealScannerCog',
    'DEAL_MODE_VIEW_TIMEOUT_SECONDS = 15 * 60\n\nlog = logging.getLogger(__name__)\n\n\nclass UnifiedDealScannerCog',
)
replace_once(
    '''        mode: str,
        force_refresh: bool = False,
    ) -> None:
        provider = provider_registry.get("walmart")
''',
    '''        mode: str,
        force_refresh: bool = False,
        replace_controls: bool = False,
        menu_token: str | None = None,
        owner_id: int | None = None,
    ) -> None:
        menu_token = menu_token or str(interaction.id)[-12:]
        owner_id = owner_id or getattr(interaction.user, "id", None)
        provider = provider_registry.get("walmart")
''',
)
replace_once(
    '''            cached_rows=cached_rows,
            freshness=freshness,
        )

        if ranked.has_verified:
''',
    '''            cached_rows=cached_rows,
            freshness=freshness,
            menu_token=menu_token,
            owner_id=owner_id,
        )

        if ranked.has_verified:
''',
)
text = text.replace(
    'await send_deal_mode_controls(interaction, view, ranked, freshness)',
    'await send_deal_mode_controls(interaction, view, ranked, freshness, replace_existing=replace_controls)',
)

old_init = '''        cached_rows: list[CachedDealRow] | None = None,
        freshness: ScanFreshness | None = None,
    ):
        super().__init__(timeout=DEAL_MODE_VIEW_TIMEOUT_SECONDS)
        self.cog = cog
        self.query = query
        self.min_discount = min_discount
        self.max_results = max_results
        self.sort_value = sort_value
        self.order_value = order_value
        self.alerts_only = alerts_only
        self.simple_mode = simple_mode
        self.mode = normalize_deal_search_mode(mode).key
        self.result = result
        self.ranked = ranked
        self.cached_rows = cached_rows or []
        self.freshness = freshness
        self.add_item(DealModeButton(MODE_BEST, row=0, disabled=self.mode == MODE_BEST))
        self.add_item(DealModeButton(MODE_POPULAR, row=0, disabled=self.mode == MODE_POPULAR))
        self.add_item(DealModeButton(MODE_HIDDEN, row=0, disabled=self.mode == MODE_HIDDEN))
        self.add_item(DealModeButton(MODE_CHEAPEST, row=1, disabled=self.mode == MODE_CHEAPEST))
        self.add_item(DealModeButton(MODE_MARKDOWN, row=1, disabled=self.mode == MODE_MARKDOWN))
        self.add_item(RefreshDealModeButton(row=1))
        self.add_item(CachedActiveButton(row=2, disabled=not bool(self.cached_rows)))
        self.add_item(NewSinceScanButton(row=2, disabled=not bool(self.freshness and (self.freshness.new_count or self.freshness.price_drop_count))))
'''
new_init = '''        cached_rows: list[CachedDealRow] | None = None,
        freshness: ScanFreshness | None = None,
        menu_token: str | None = None,
        owner_id: int | None = None,
    ):
        super().__init__(timeout=DEAL_MODE_VIEW_TIMEOUT_SECONDS)
        self.cog = cog
        self.query = query
        self.min_discount = min_discount
        self.max_results = max_results
        self.sort_value = sort_value
        self.order_value = order_value
        self.alerts_only = alerts_only
        self.simple_mode = simple_mode
        self.mode = normalize_deal_search_mode(mode).key
        self.result = result
        self.ranked = ranked
        self.cached_rows = cached_rows or []
        self.freshness = freshness
        self.menu_token = menu_token or "legacy"
        self.owner_id = owner_id
        self.add_item(DealModeButton(MODE_BEST, row=0, active=self.mode == MODE_BEST, menu_token=self.menu_token))
        self.add_item(DealModeButton(MODE_POPULAR, row=0, active=self.mode == MODE_POPULAR, menu_token=self.menu_token))
        self.add_item(DealModeButton(MODE_HIDDEN, row=0, active=self.mode == MODE_HIDDEN, menu_token=self.menu_token))
        self.add_item(DealModeButton(MODE_CHEAPEST, row=1, active=self.mode == MODE_CHEAPEST, menu_token=self.menu_token))
        self.add_item(DealModeButton(MODE_MARKDOWN, row=1, active=self.mode == MODE_MARKDOWN, menu_token=self.menu_token))
        self.add_item(RefreshDealModeButton(row=1, menu_token=self.menu_token))
        self.add_item(CachedActiveButton(row=2, disabled=not bool(self.cached_rows), menu_token=self.menu_token))
        self.add_item(NewSinceScanButton(row=2, disabled=not bool(self.freshness and (self.freshness.new_count or self.freshness.price_drop_count)), menu_token=self.menu_token))
'''
replace_once(old_init, new_init)

view_methods = '''    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and getattr(interaction.user, "id", None) != self.owner_id:
            await deal_scanner.safe_send_interaction(
                interaction,
                "Run your own `/deals` search to use these controls.",
                ephemeral=True,
            )
            return False
        if interaction.response.is_done():
            return True
        try:
            await interaction.response.defer(thinking=False)
            log.info(
                "Deal result interaction acknowledged custom_id=%s interaction_id=%s message_id=%s mode=%s",
                (getattr(interaction, "data", None) or {}).get("custom_id"),
                getattr(interaction, "id", None),
                getattr(getattr(interaction, "message", None), "id", None),
                self.mode,
            )
            return True
        except discord.InteractionResponded:
            return True
        except (discord.NotFound, discord.HTTPException) as exc:
            log.warning(
                "Deal result interaction acknowledgement failed custom_id=%s interaction_id=%s message_id=%s mode=%s error=%r",
                (getattr(interaction, "data", None) or {}).get("custom_id"),
                getattr(interaction, "id", None),
                getattr(getattr(interaction, "message", None), "id", None),
                self.mode,
                exc,
            )
            return False

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
        /,
    ) -> None:
        log.error(
            "Deal result control failed custom_id=%s interaction_id=%s message_id=%s mode=%s",
            getattr(item, "custom_id", None),
            getattr(interaction, "id", None),
            getattr(getattr(interaction, "message", None), "id", None),
            self.mode,
            exc_info=(type(error), error, error.__traceback__),
        )
        await deal_scanner.safe_send_interaction(
            interaction,
            "That result button hit an internal error. It was logged; run `/deals` again if the retry also fails.",
            ephemeral=True,
        )

'''
replace_once(
    '    async def show_mode(self, interaction: discord.Interaction, mode: str, *, refresh: bool = False) -> None:\n',
    view_methods + '    async def show_mode(self, interaction: discord.Interaction, mode: str, *, refresh: bool = False) -> None:\n',
)
replace_once(
    '''        if not await deal_scanner.safe_defer(interaction, ephemeral=True, thinking=True):
            return
        if refresh or self.result is None:
''',
    '''        if not interaction.response.is_done():
            if not await deal_scanner.safe_defer(interaction, ephemeral=False, thinking=False):
                return
        if refresh or self.result is None:
''',
)
replace_once(
    '''                mode=mode,
                force_refresh=refresh,
            )
''',
    '''                mode=mode,
                force_refresh=refresh,
                replace_controls=True,
                menu_token=self.menu_token,
                owner_id=self.owner_id,
            )
''',
)
replace_once(
    '''            cached_rows=self.cached_rows,
            freshness=self.freshness,
        )
        if ranked.has_verified:
''',
    '''            cached_rows=self.cached_rows,
            freshness=self.freshness,
            menu_token=self.menu_token,
            owner_id=self.owner_id,
        )
        if ranked.has_verified:
''',
)
text = text.replace(
    'await send_deal_mode_controls(interaction, view, ranked, self.freshness)',
    'await send_deal_mode_controls(interaction, view, ranked, self.freshness, replace_existing=True)',
)

classes_start = text.index('class DealModeButton(discord.ui.Button):')
controls_start = text.index('async def send_deal_mode_controls', classes_start)
new_classes = '''class DealModeButton(discord.ui.Button):
    def __init__(self, mode: str, *, row: int, active: bool = False, menu_token: str):
        info = DEAL_SEARCH_MODES[mode]
        super().__init__(
            label=info.label,
            emoji=info.emoji,
            style=discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary,
            row=row,
            disabled=active,
            custom_id=f"deal_mode:{menu_token}:{mode}",
        )
        self.mode = mode

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, DealSearchModeView):
            await deal_scanner.safe_send_interaction(interaction, "This result menu is no longer active.", ephemeral=True)
            return
        await self.view.show_mode(interaction, self.mode)


class RefreshDealModeButton(discord.ui.Button):
    def __init__(self, *, row: int, menu_token: str):
        super().__init__(
            label="Fresh Scan",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            row=row,
            custom_id=f"deal_mode:{menu_token}:refresh",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, DealSearchModeView):
            await deal_scanner.safe_send_interaction(interaction, "This result menu is no longer active.", ephemeral=True)
            return
        await self.view.show_mode(interaction, self.view.mode, refresh=True)


class CachedActiveButton(discord.ui.Button):
    def __init__(self, *, row: int, disabled: bool = False, menu_token: str):
        super().__init__(
            label="Cached Active",
            emoji="⚡",
            style=discord.ButtonStyle.success,
            row=row,
            disabled=disabled,
            custom_id=f"deal_mode:{menu_token}:cached_active",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, DealSearchModeView):
            await deal_scanner.safe_send_interaction(interaction, "This result menu is no longer active.", ephemeral=True)
            return
        await interaction.followup.send(
            embed=sanitize_embed(build_cached_active_embed(self.view.query, self.view.cached_rows)),
            ephemeral=True,
        )


class NewSinceScanButton(discord.ui.Button):
    def __init__(self, *, row: int, disabled: bool = False, menu_token: str):
        super().__init__(
            label="New / Drops",
            emoji="🆕",
            style=discord.ButtonStyle.danger,
            row=row,
            disabled=disabled,
            custom_id=f"deal_mode:{menu_token}:new_since_scan",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, DealSearchModeView) or self.view.freshness is None:
            await deal_scanner.safe_send_interaction(interaction, "This result menu is no longer active.", ephemeral=True)
            return
        freshness = self.view.freshness
        cards = [*freshness.price_drop_cards[:5], *freshness.new_cards[:5]][:5]
        await interaction.followup.send(
            embed=sanitize_embed(build_new_since_scan_embed(self.view.query, freshness)),
            ephemeral=True,
        )
        for card in cards:
            await interaction.followup.send(embed=sanitize_embed(card.embed), ephemeral=True)


'''
text = text[:classes_start] + new_classes + text[controls_start:]

old_controls = '''async def send_deal_mode_controls(interaction: discord.Interaction, view: DealSearchModeView, ranked: ModeRankedCards, freshness: ScanFreshness | None = None) -> None:
    await interaction.followup.send(embed=sanitize_embed(build_deal_mode_menu_embed(view.query, ranked, freshness)), view=view, ephemeral=True)
'''
new_controls = '''async def send_deal_mode_controls(
    interaction: discord.Interaction,
    view: DealSearchModeView,
    ranked: ModeRankedCards,
    freshness: ScanFreshness | None = None,
    *,
    replace_existing: bool = False,
) -> None:
    embed = sanitize_embed(build_deal_mode_menu_embed(view.query, ranked, freshness))
    if replace_existing:
        try:
            await interaction.edit_original_response(embed=embed, view=view)
            return
        except (discord.NotFound, discord.HTTPException) as exc:
            log.warning(
                "Deal result menu edit failed; sending replacement menu interaction_id=%s message_id=%s error=%r",
                getattr(interaction, "id", None),
                getattr(getattr(interaction, "message", None), "id", None),
                exc,
            )
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
'''
replace_once(old_controls, new_controls)

path.write_text(text)
<!-- DEALS_PATCH_END -->