from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.deal_scanner import (
    BEGINNER_FALLBACK_DISCOUNTS,
    HUNT_PRESETS,
    PRESET_FALLBACK_DISCOUNTS,
    SORT_CHOICES,
    DealCard,
    HuntPreset,
    build_hunt_menu_embed,
    build_hunt_summary,
    build_preset_hunt_summary,
    build_scan_summary,
    build_walmart_cards,
    cards_with_fallback,
    dedupe_candidates,
    no_match_help,
    parse_sort_choice,
    provider_health_error_message,
    run_walmart_scan,
)
from sniperplug.providers.base import ProviderScanResult, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.operation_locks import (
    OperationLockTicket,
    make_scan_operation_key,
    scan_operation_locks,
)


class DealScannerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name='hunt', description='Tap a category and let SniperPlug hunt deals for you.')
    async def hunt(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=build_hunt_menu_embed(), view=HuntPresetMenuView(), ephemeral=True)

    @app_commands.command(name='deals', description='Find deals the easy way. Just type what you want.')
    @app_commands.describe(search='What are you shopping for? Example: monitor, lego, tide, patio set, air fryer.')
    async def deals(self, interaction: discord.Interaction, search: str) -> None:
        key = scan_lock_key(interaction, 'slash_deals', search, 1, 50)
        ticket = await acquire_scan_lock(interaction, key, disable_view=False)
        if ticket is None:
            return
        try:
            await interaction.response.defer(ephemeral=True)
            await self._send_walmart_scan(interaction, search, 50, 1, 10, None, None, False, True)
        finally:
            await scan_operation_locks.release(ticket)

    @app_commands.command(name='walmart_scan', description='Advanced Walmart deal scan for staff/admins.')
    @app_commands.describe(
        query='Product search, like gaming monitor, tide detergent, lego, patio set.',
        min_discount='Only show deals at or above this percent off. Try 50 or 80.',
        page='Walmart result page to scan. Use page 2/3 if page 1 repeats weak deals.',
        max_results='How many Walmart results to inspect on this page. Max 25.',
        sort='Optional Walmart sorting mode.',
        alerts_only='Only show candidates SniperPlug would alert on.',
    )
    @app_commands.choices(sort=SORT_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def walmart_scan(
        self,
        interaction: discord.Interaction,
        query: str,
        min_discount: app_commands.Range[int, 0, 95] = 30,
        page: app_commands.Range[int, 1, 40] = 1,
        max_results: app_commands.Range[int, 1, 25] = 10,
        sort: app_commands.Choice[str] | None = None,
        alerts_only: bool = False,
    ) -> None:
        sort_value, order_value = parse_sort_choice(sort.value if sort else None)
        key = scan_lock_key(interaction, 'slash_walmart_scan', query, page, min_discount, max_results, sort_value, order_value, alerts_only)
        ticket = await acquire_scan_lock(interaction, key, disable_view=False)
        if ticket is None:
            return
        try:
            await interaction.response.defer(ephemeral=True)
            await self._send_walmart_scan(interaction, query, min_discount, page, max_results, sort_value, order_value, alerts_only, False)
        finally:
            await scan_operation_locks.release(ticket)

    async def _send_walmart_scan(
        self,
        interaction: discord.Interaction,
        query: str,
        min_discount: int,
        page: int,
        max_results: int,
        sort_value: str | None,
        order_value: str | None,
        alerts_only: bool,
        simple_mode: bool,
    ) -> None:
        provider = provider_registry.get('walmart')
        if provider is None:
            await interaction.followup.send('Walmart search is not connected yet.', ephemeral=True)
            return

        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send('Deal search is not ready yet. Staff needs to finish the Walmart connection first.', ephemeral=True)
            return

        result = await run_walmart_scan(query, page, max_results, sort_value, order_value, str(interaction.user.id))
        if simple_mode:
            cards, shown_discount = cards_with_fallback(result, min_discount, alerts_only, BEGINNER_FALLBACK_DISCOUNTS)
        else:
            cards = build_walmart_cards(result, min_discount=min_discount, alerts_only=alerts_only)
            shown_discount = min_discount

        summary = build_scan_summary(result, query, min_discount, shown_discount, alerts_only, simple_mode)
        if not cards:
            summary.add_field(name='Nothing useful found yet', value=no_match_help(query, min_discount, page, simple_mode), inline=False)
            view = DealSearchControlView(query, page, max(0, shown_discount), max_results, sort_value, order_value, alerts_only, simple_mode)
            await interaction.followup.send(embed=summary, view=view, ephemeral=True)
            return

        view = DealSearchControlView(query, page, max(0, shown_discount), max_results, sort_value, order_value, alerts_only, simple_mode, cards[:5], result.has_next_page)
        await interaction.followup.send(embeds=[summary] + [card.embed for card in cards[:5]], view=view, ephemeral=True)

    @hunt.error
    async def hunt_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await send_command_error(interaction, f'Deal hunt hit an error: `{error}`')

    @deals.error
    async def deals_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await send_command_error(interaction, f'Deal search hit an error: `{error}`')

    @walmart_scan.error
    async def walmart_scan_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = 'You need **Manage Server** permission to run advanced Walmart scans. Use `/deals` for the simple search.'
        else:
            message = f'Walmart scan hit an error: `{error}`'
        await send_command_error(interaction, message)


class HuntPresetMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(HuntPresetButton(HUNT_PRESETS['glitch'], row=0))
        self.add_item(HuntPresetButton(HUNT_PRESETS['tech'], row=0))
        self.add_item(HuntPresetButton(HUNT_PRESETS['essentials'], row=0))
        self.add_item(HuntPresetButton(HUNT_PRESETS['home'], row=1))
        self.add_item(HuntPresetButton(HUNT_PRESETS['toys'], row=1))
        self.add_item(HuntPresetButton(HUNT_PRESETS['auto_tools'], row=1))


class HuntPresetButton(discord.ui.Button):
    def __init__(self, preset: HuntPreset, row: int):
        super().__init__(label=preset.label, emoji=preset.emoji, style=discord.ButtonStyle.primary, row=row, custom_id=f'hunt_preset:{preset.key}')
        self.preset = preset

    async def callback(self, interaction: discord.Interaction) -> None:
        key = scan_lock_key(interaction, 'hunt_preset', self.preset.key, self.preset.min_discount)
        ticket = await acquire_scan_lock(
            interaction,
            key,
            disable_view=True,
            view=self.view,
            working_message=f'⏳ SniperPlug is running **{self.preset.label}**. Buttons are disabled so it cannot double-post.',
        )
        if ticket is None:
            return
        try:
            health_error = await provider_health_error_message()
            if health_error:
                await interaction.followup.send(health_error, ephemeral=True)
                return

            cards, pages_checked, products_checked, warnings, shown_discount = await run_preset_hunt_locked(self.preset, str(interaction.user.id))
            summary = build_preset_hunt_summary(self.preset, pages_checked, products_checked, len(cards), tuple(warnings), shown_discount)
            if not cards:
                summary.add_field(
                    name='Nothing useful found yet',
                    value='I checked multiple smart searches and still could not prove a useful markdown.\nTry another category, or use `/deals search:` with a specific item like `oled tv`, `lego`, `detergent`, or `ssd`.',
                    inline=False,
                )
                await interaction.followup.send(embed=summary, view=HuntPresetMenuView(), ephemeral=True)
                return
            await interaction.followup.send(embeds=[summary] + [card.embed for card in cards[:5]], view=PresetResultView(cards[:5]), ephemeral=True)
        finally:
            await scan_operation_locks.release(ticket)


class PresetResultView(discord.ui.View):
    def __init__(self, cards: list[DealCard]):
        super().__init__(timeout=300)
        for idx, card in enumerate(cards, start=1):
            self.add_item(discord.ui.Button(label=f'View Deal {idx}', style=discord.ButtonStyle.link, url=card.url))
        self.add_item(discord.ui.Button(label='Run /hunt again for more categories', style=discord.ButtonStyle.secondary, disabled=True, row=1))


class DealSearchControlView(discord.ui.View):
    def __init__(
        self,
        query: str,
        page: int,
        min_discount: int,
        max_results: int,
        sort_value: str | None,
        order_value: str | None,
        alerts_only: bool,
        simple_mode: bool,
        cards: list[DealCard] | None = None,
        has_next_page: bool = False,
    ):
        super().__init__(timeout=300)
        self.query = query
        self.page = page
        self.min_discount = min_discount
        self.max_results = max_results
        self.sort_value = sort_value
        self.order_value = order_value
        self.alerts_only = alerts_only
        self.simple_mode = simple_mode
        self.has_next_page = has_next_page

        for idx, card in enumerate(cards or [], start=1):
            self.add_item(discord.ui.Button(label=f'View Deal {idx}', style=discord.ButtonStyle.link, url=card.url))

    @discord.ui.button(label='Next Page', emoji='➡️', style=discord.ButtonStyle.primary, row=1)
    async def next_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._rerun(interaction, self.page + 1, self.min_discount, 'next_page')

    @discord.ui.button(label='Hunt 80%+', emoji='🔥', style=discord.ButtonStyle.secondary, row=1)
    async def huge_discounts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._hunt_pages(interaction, min_discount=80, max_pages=5)

    @discord.ui.button(label='More Matches', emoji='🔎', style=discord.ButtonStyle.secondary, row=1)
    async def show_more(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._rerun(interaction, self.page, max(0, self.min_discount - 20), 'more_matches')

    async def _rerun(self, interaction: discord.Interaction, page: int, min_discount: int, action_name: str) -> None:
        key = scan_lock_key(interaction, action_name, self.query, page, min_discount, self.max_results, self.sort_value, self.order_value, self.alerts_only)
        ticket = await acquire_scan_lock(interaction, key, disable_view=True, view=self, working_message='⏳ SniperPlug is searching. This click is locked so it cannot double-post.')
        if ticket is None:
            return
        try:
            result = await run_walmart_scan(self.query, page, self.max_results, self.sort_value, self.order_value, str(interaction.user.id))
            if self.simple_mode:
                cards, shown_discount = cards_with_fallback(result, min_discount, self.alerts_only, BEGINNER_FALLBACK_DISCOUNTS)
            else:
                cards = build_walmart_cards(result, min_discount=min_discount, alerts_only=self.alerts_only)
                shown_discount = min_discount
            summary = build_scan_summary(result, self.query, min_discount, shown_discount, self.alerts_only, self.simple_mode)
            if not cards:
                summary.add_field(name='Nothing useful found yet', value=no_match_help(self.query, min_discount, page, self.simple_mode), inline=False)
                await interaction.followup.send(embed=summary, view=self._copy_for(page, shown_discount, [], result.has_next_page), ephemeral=True)
                return
            await interaction.followup.send(embeds=[summary] + [card.embed for card in cards[:5]], view=self._copy_for(page, shown_discount, cards[:5], result.has_next_page), ephemeral=True)
        finally:
            await scan_operation_locks.release(ticket)

    async def _hunt_pages(self, interaction: discord.Interaction, min_discount: int, max_pages: int) -> None:
        key = scan_lock_key(interaction, 'hunt_pages', self.query, 1, min_discount, max_pages, self.sort_value, self.order_value, self.alerts_only)
        ticket = await acquire_scan_lock(interaction, key, disable_view=True, view=self, working_message=f'⏳ SniperPlug is scanning up to **{max_pages} pages** for **{min_discount}%+** deals. Duplicate clicks are blocked.')
        if ticket is None:
            return
        try:
            all_candidates = []
            warnings: list[str] = []
            pages_checked = 0
            total_results = None
            has_next_page = False

            for page in range(1, 1 + max_pages):
                result = await run_walmart_scan(self.query, page, 25, self.sort_value, self.order_value, str(interaction.user.id))
                pages_checked += 1
                all_candidates.extend(result.candidates)
                warnings.extend(w for w in result.warnings if w not in warnings)
                total_results = result.total_results if result.total_results is not None else total_results
                has_next_page = result.has_next_page

                aggregate = ProviderScanResult(
                    provider_key=result.provider_key,
                    candidates=tuple(dedupe_candidates(all_candidates)),
                    warnings=tuple(warnings),
                    total_results=total_results,
                    page=page,
                    page_size=25,
                    start_index=1,
                    has_next_page=has_next_page,
                )
                cards = build_walmart_cards(aggregate, min_discount=min_discount, alerts_only=self.alerts_only)
                if cards:
                    cards.sort(key=lambda card: (card.discount, card.score), reverse=True)
                    summary = build_hunt_summary(self.query, min_discount, pages_checked, len(all_candidates), total_results, len(cards), tuple(warnings), self.simple_mode)
                    await interaction.followup.send(embeds=[summary] + [card.embed for card in cards[:5]], view=self._copy_for(page, min_discount, cards[:5], has_next_page), ephemeral=True)
                    return
                if not result.has_next_page:
                    break

            aggregate = ProviderScanResult(
                provider_key='walmart',
                candidates=tuple(dedupe_candidates(all_candidates)),
                warnings=tuple(warnings),
                total_results=total_results,
                page=1,
                page_size=len(all_candidates),
                start_index=1,
                has_next_page=has_next_page,
            )
            fallback_cards, shown_discount = cards_with_fallback(aggregate, 50, self.alerts_only, (50, 30, 10))
            summary = build_hunt_summary(self.query, min_discount, pages_checked, len(all_candidates), total_results, len(fallback_cards), tuple(warnings), self.simple_mode)
            if fallback_cards:
                summary.add_field(name='No 80%+ found — showing closest matches', value=f'I did not find a true 80%+ markdown, so I’m showing the best **{shown_discount}%+** matches instead.', inline=False)
                await interaction.followup.send(embeds=[summary] + [card.embed for card in fallback_cards[:5]], view=self._copy_for(self.page, shown_discount, fallback_cards[:5], has_next_page), ephemeral=True)
                return
            summary.add_field(
                name='No useful markdowns found yet',
                value=f'I checked **{len(all_candidates)} products across {pages_checked} page(s)** and could not prove a strong markdown.\nTry another search like `iphone case`, `iphone charger`, `oled tv`, `clearance toy`, or run `/hunt` and tap a category.',
                inline=False,
            )
            await interaction.followup.send(embed=summary, view=self._copy_for(self.page, min_discount, [], has_next_page), ephemeral=True)
        finally:
            await scan_operation_locks.release(ticket)

    def _copy_for(self, page: int, min_discount: int, cards: list[DealCard], has_next_page: bool) -> 'DealSearchControlView':
        return DealSearchControlView(self.query, page, min_discount, self.max_results, self.sort_value, self.order_value, self.alerts_only, self.simple_mode, cards, has_next_page)


async def run_preset_hunt_locked(preset: HuntPreset, requested_by: str) -> tuple[list[DealCard], int, int, list[str], int]:
    all_candidates = []
    warnings: list[str] = []
    pages_checked = 0
    for query in preset.queries[:5]:
        result = await run_walmart_scan(query, 1, 12, None, None, requested_by)
        pages_checked += 1
        all_candidates.extend(result.candidates)
        warnings.extend(w for w in result.warnings if w not in warnings)

    aggregate = ProviderScanResult(
        provider_key='walmart',
        candidates=tuple(dedupe_candidates(all_candidates)),
        warnings=tuple(warnings),
        page=1,
        page_size=len(all_candidates),
        start_index=1,
        has_next_page=True,
    )
    fallback_chain = tuple(x for x in (preset.min_discount, *PRESET_FALLBACK_DISCOUNTS) if x <= preset.min_discount)
    cards, shown_discount = cards_with_fallback(aggregate, preset.min_discount, alerts_only=False, fallback_discounts=fallback_chain)
    cards.sort(key=lambda card: (card.discount, card.score), reverse=True)
    return cards, pages_checked, len(all_candidates), warnings, shown_discount


def scan_lock_key(interaction: discord.Interaction, action: str, *parts: object) -> str:
    return make_scan_operation_key(
        guild_id=getattr(interaction, 'guild_id', None),
        user_id=getattr(getattr(interaction, 'user', None), 'id', None),
        action=action,
        parts=parts,
    )


async def acquire_scan_lock(
    interaction: discord.Interaction,
    key: str,
    *,
    disable_view: bool,
    view: discord.ui.View | None = None,
    working_message: str = '⏳ SniperPlug is already working on that scan.',
) -> OperationLockTicket | None:
    ticket = await scan_operation_locks.acquire(key)
    if ticket is None:
        await send_duplicate_scan_blocked(interaction)
        return None
    if disable_view:
        disable_view_items(view)
        await mark_interaction_working(interaction, working_message, view)
    return ticket


async def send_duplicate_scan_blocked(interaction: discord.Interaction) -> None:
    message = '⛔ That scan is already running. SniperPlug blocked the duplicate click to prevent duplicate posts.'
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def mark_interaction_working(interaction: discord.Interaction, working_message: str, view: discord.ui.View | None) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(working_message, ephemeral=True)
    else:
        await interaction.response.edit_message(content=working_message, view=view)


def disable_view_items(view: discord.ui.View | None) -> None:
    if view is None:
        return
    for child in view.children:
        if hasattr(child, 'disabled'):
            child.disabled = True


async def send_command_error(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
