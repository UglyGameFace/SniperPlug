from __future__ import annotations

from dataclasses import replace
from math import ceil
from typing import Any

import discord

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    save_dm_deal_alert_preference,
)
from sniperplug.services.dm_personal_categories import (
    all_personal_categories,
    category_label,
    compose_category_preferences,
    compose_exclude_terms,
    flip_settings,
    muted_category_preferences,
    personal_category_pages,
    split_category_preferences,
    split_exclude_terms,
)


TAB_FAVORITES = "favorites"
TAB_MUTED = "muted"
TAB_ALLOWLIST = "allowlist"
TAB_LABELS = {
    TAB_FAVORITES: "⭐ Favorites",
    TAB_MUTED: "🔕 Muted",
    TAB_ALLOWLIST: "🎯 Hard allowlist",
}


class DmDealPreferencesView(discord.ui.View):
    """Private, complete-category editor for one DM subscriber."""

    def __init__(
        self,
        *,
        bot: Any,
        user_id: int,
        preference: DmDealAlertPreference,
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.bot = bot
        self.user_id = int(user_id)
        self.preference = preference.normalized()

        selected, favorites = split_category_preferences(
            self.preference.categories
        )
        keyword_excludes, legacy_muted = split_exclude_terms(
            self.preference.exclude_keywords
        )
        stored_muted = muted_category_preferences(
            self.preference.categories
        )
        flip_enabled, flip_profit = flip_settings(
            self.preference.categories
        )

        self.selected_categories = set(selected)
        self.favorite_categories = set(favorites)
        self.muted_categories = set((*legacy_muted, *stored_muted))
        self.exclude_keywords = tuple(keyword_excludes)
        self.flip_enabled = bool(flip_enabled)
        self.flip_min_profit_cents = int(flip_profit)

        self.tab = TAB_FAVORITES
        self.search_query = ""
        self.page_index = 0
        self.saved = False
        self._rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.user_id:
            return True
        await interaction.response.send_message(
            "This is someone else's private SniperPlug preference menu.",
            ephemeral=True,
        )
        return False

    def build_embed(self, *, status: str = "") -> discord.Embed:
        categories = all_personal_categories()
        pages = self._pages()
        page_total = max(1, len(pages))
        page_number = min(self.page_index + 1, page_total)
        search_text = self.search_query or "all categories"

        tab_help = {
            TAB_FAVORITES: (
                "Favorites receive a small Smart-mode priority boost. They are "
                "not an allowlist, so excellent deals in other categories remain eligible."
            ),
            TAB_MUTED: (
                "Muted categories disappear from normal personal DMs only. A strict "
                "Flip Override may still surface an exceptional resale opportunity."
            ),
            TAB_ALLOWLIST: (
                "This is optional and strict. Leave it empty to keep every category "
                "eligible; select categories only when you truly want an allowlist."
            ),
        }[self.tab]

        description = (
            f"**Every live category is available:** {len(categories)} total. "
            "Pages are only Discord's 25-option display limit; SniperPlug does not "
            "trim the catalog.\n\n"
            f"Editing: **{TAB_LABELS[self.tab]}** • Page **{page_number}/{page_total}** "
            f"• Search: **{search_text}**\n{tab_help}"
        )
        if status:
            description = f"{status}\n\n{description}"

        embed = discord.Embed(
            title="🎛️ Personal Deal DM Menu",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Your category choices",
            value=(
                f"⭐ Favorites: **{len(self.favorite_categories)}**\n"
                f"🔕 Muted: **{len(self.muted_categories)}**\n"
                f"🎯 Allowlist: **{len(self.selected_categories) or 'off — all categories'}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Price-error / Flip Override",
            value=(
                f"Status: **{'on' if self.flip_enabled else 'off'}**\n"
                f"Minimum estimated net: **${self.flip_min_profit_cents / 100:,.2f}**\n"
                "Recent eBay sold comps: **used when exact evidence is connected**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Selected in this tab",
            value=self._selection_preview(),
            inline=False,
        )
        embed.add_field(
            name="Safety boundary",
            value=(
                "Flip Override crosses category boundaries only. Exact item, seller, offer, "
                "variant, availability, current/was-price proof, your explicit floors, "
                "excluded words, maximum price, dedupe, and daily cap remain enforced. "
                "Without sold comps, resale value is labeled **estimated**, never confirmed."
            ),
            inline=False,
        )
        embed.set_footer(
            text="Selections are drafts until you press Save. Search never removes categories."
        )
        return embed

    def _pages(self):
        return personal_category_pages(self.search_query)

    def _active_set(self) -> set[str]:
        if self.tab == TAB_MUTED:
            return self.muted_categories
        if self.tab == TAB_ALLOWLIST:
            return self.selected_categories
        return self.favorite_categories

    def _current_page(self):
        pages = self._pages()
        if not pages:
            self.page_index = 0
            return ()
        self.page_index = max(0, min(self.page_index, len(pages) - 1))
        return pages[self.page_index]

    def _selection_preview(self) -> str:
        selected = sorted(
            self._active_set(),
            key=lambda key: category_label(key).lower(),
        )
        if not selected:
            return (
                "none"
                if self.tab != TAB_ALLOWLIST
                else "none — every category remains eligible"
            )
        labels = [category_label(key) for key in selected[:12]]
        suffix = f" • +{len(selected) - 12} more" if len(selected) > 12 else ""
        return ", ".join(labels) + suffix

    def _rebuild_items(self) -> None:
        self.clear_items()
        self.add_item(_PreferenceTabSelect(self))
        self.add_item(_CategoryPageSelect(self))

        pages = self._pages()
        last_index = max(0, len(pages) - 1)

        previous = discord.ui.Button(
            label="Back",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=self.page_index <= 0,
        )
        previous.callback = self._previous_page
        self.add_item(previous)

        page = discord.ui.Button(
            label=f"Page {min(self.page_index + 1, max(1, len(pages)))}/{max(1, len(pages))}",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=True,
        )
        self.add_item(page)

        next_button = discord.ui.Button(
            label="Next",
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=(not pages or self.page_index >= last_index),
        )
        next_button.callback = self._next_page
        self.add_item(next_button)

        search = discord.ui.Button(
            label="Search",
            emoji="🔎",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        search.callback = self._open_search
        self.add_item(search)

        clear_search = discord.ui.Button(
            label="All categories",
            emoji="🧹",
            style=discord.ButtonStyle.secondary,
            row=2,
            disabled=not bool(self.search_query),
        )
        clear_search.callback = self._clear_search
        self.add_item(clear_search)

        clear_tab = discord.ui.Button(
            label=f"Clear {TAB_LABELS[self.tab].split(' ', 1)[1]}",
            emoji="🗑️",
            style=discord.ButtonStyle.secondary,
            row=3,
        )
        clear_tab.callback = self._clear_current_tab
        self.add_item(clear_tab)

        flip = discord.ui.Button(
            label=f"Flip Override: {'ON' if self.flip_enabled else 'OFF'}",
            emoji="💰",
            style=(
                discord.ButtonStyle.success
                if self.flip_enabled
                else discord.ButtonStyle.secondary
            ),
            row=3,
        )
        flip.callback = self._toggle_flip
        self.add_item(flip)

        profit = discord.ui.Button(
            label=f"Profit: ${self.flip_min_profit_cents / 100:,.0f}+",
            emoji="📈",
            style=discord.ButtonStyle.secondary,
            row=3,
        )
        profit.callback = self._open_profit
        self.add_item(profit)

        save = discord.ui.Button(
            label="Save",
            emoji="✅",
            style=discord.ButtonStyle.success,
            row=3,
        )
        save.callback = self._save
        self.add_item(save)

        cancel = discord.ui.Button(
            label="Cancel",
            emoji="✖️",
            style=discord.ButtonStyle.danger,
            row=3,
        )
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _refresh(self, interaction: discord.Interaction, *, status: str = "") -> None:
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=self.build_embed(status=status),
            view=self,
        )

    async def _previous_page(self, interaction: discord.Interaction) -> None:
        self.page_index = max(0, self.page_index - 1)
        await self._refresh(interaction)

    async def _next_page(self, interaction: discord.Interaction) -> None:
        pages = self._pages()
        self.page_index = min(max(0, len(pages) - 1), self.page_index + 1)
        await self._refresh(interaction)

    async def _open_search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_CategorySearchModal(self))

    async def _clear_search(self, interaction: discord.Interaction) -> None:
        self.search_query = ""
        self.page_index = 0
        await self._refresh(interaction, status="✅ Showing the complete category catalog again.")

    async def _clear_current_tab(self, interaction: discord.Interaction) -> None:
        self._active_set().clear()
        await self._refresh(
            interaction,
            status=f"🧹 Cleared the draft {TAB_LABELS[self.tab]} selection.",
        )

    async def _toggle_flip(self, interaction: discord.Interaction) -> None:
        self.flip_enabled = not self.flip_enabled
        await self._refresh(
            interaction,
            status=(
                "💰 Flip Override enabled for strict cross-category opportunities."
                if self.flip_enabled
                else "🔕 Flip Override disabled. Category mutes remain absolute."
            ),
        )

    async def _open_profit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_FlipProfitModal(self))

    async def _save(self, interaction: discord.Interaction) -> None:
        categories = compose_category_preferences(
            self.selected_categories,
            self.favorite_categories,
            muted=self.muted_categories,
            flip_enabled=self.flip_enabled,
            flip_min_profit_cents=self.flip_min_profit_cents,
        )
        # Saving through the menu migrates legacy category tokens out of the
        # capped free-form keyword field.
        excludes = compose_exclude_terms(self.exclude_keywords)
        updated = replace(
            self.preference,
            categories=categories,
            exclude_keywords=excludes,
            failure_count=0,
            last_error="",
        ).normalized()
        saved = await save_dm_deal_alert_preference(self.bot.db, updated)
        self.preference = saved
        self.saved = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=self.build_embed(status="✅ Your personal DM menu was saved."),
            view=self,
        )
        self.stop()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=self.build_embed(status="No changes were saved."),
            view=self,
        )
        self.stop()


class _PreferenceTabSelect(discord.ui.Select):
    def __init__(self, owner: DmDealPreferencesView) -> None:
        self.owner = owner
        options = [
            discord.SelectOption(
                label="Favorites",
                value=TAB_FAVORITES,
                emoji="⭐",
                description="Prioritize interests without hiding other deals.",
                default=owner.tab == TAB_FAVORITES,
            ),
            discord.SelectOption(
                label="Muted",
                value=TAB_MUTED,
                emoji="🔕",
                description="Hide categories from normal personal DMs.",
                default=owner.tab == TAB_MUTED,
            ),
            discord.SelectOption(
                label="Hard allowlist",
                value=TAB_ALLOWLIST,
                emoji="🎯",
                description="Optional strict category-only delivery filter.",
                default=owner.tab == TAB_ALLOWLIST,
            ),
        ]
        super().__init__(
            placeholder="Choose what you are editing",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.owner.tab = self.values[0]
        self.owner.page_index = 0
        await self.owner._refresh(interaction)


class _CategoryPageSelect(discord.ui.Select):
    def __init__(self, owner: DmDealPreferencesView) -> None:
        self.owner = owner
        page = owner._current_page()
        active = owner._active_set()

        if not page:
            options = [
                discord.SelectOption(
                    label="No categories match this search",
                    value="__none__",
                    description="Use Search again or press All categories.",
                )
            ]
            super().__init__(
                placeholder="No matching categories",
                min_values=1,
                max_values=1,
                options=options,
                disabled=True,
                row=1,
            )
            return

        options = [
            discord.SelectOption(
                label=category.label[:100],
                value=category.key,
                description=(
                    f"Demand {category.demand_level}/100 • discovery baseline "
                    f"{category.min_discount_percent:g}%"
                )[:100],
                default=category.key in active,
            )
            for category in page
        ]
        super().__init__(
            placeholder=f"Select {TAB_LABELS[owner.tab].split(' ', 1)[1].lower()} on this page",
            min_values=0,
            max_values=len(options),
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        page_keys = {category.key for category in self.owner._current_page()}
        selected = {value for value in self.values if value != "__none__"}
        active = self.owner._active_set()
        active.difference_update(page_keys)
        active.update(selected)

        # A category cannot be both favorite and muted in the saved personal UI.
        if self.owner.tab == TAB_FAVORITES:
            self.owner.muted_categories.difference_update(selected)
        elif self.owner.tab == TAB_MUTED:
            self.owner.favorite_categories.difference_update(selected)

        await self.owner._refresh(
            interaction,
            status=(
                f"Draft updated: {len(selected)} selected on this page. "
                "Press Save when finished."
            ),
        )


class _CategorySearchModal(discord.ui.Modal, title="Search every deal category"):
    query = discord.ui.TextInput(
        label="Category, product, or interest",
        placeholder="Examples: tech, gaming, baby, sneakers, tools, coffee",
        required=False,
        max_length=100,
    )

    def __init__(self, owner: DmDealPreferencesView) -> None:
        super().__init__()
        self.owner = owner
        self.query.default = owner.search_query

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.owner.search_query = " ".join(str(self.query.value or "").split())
        self.owner.page_index = 0
        await self.owner._refresh(
            interaction,
            status=(
                f"🔎 Search applied: **{self.owner.search_query}**"
                if self.owner.search_query
                else "✅ Showing every category."
            ),
        )


class _FlipProfitModal(discord.ui.Modal, title="Flip Override minimum profit"):
    amount = discord.ui.TextInput(
        label="Minimum estimated net profit in dollars",
        placeholder="50",
        required=True,
        max_length=12,
    )

    def __init__(self, owner: DmDealPreferencesView) -> None:
        super().__init__()
        self.owner = owner
        self.amount.default = f"{owner.flip_min_profit_cents / 100:.2f}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.amount.value or "").replace("$", "").replace(",", "").strip()
        try:
            dollars = float(raw)
        except ValueError:
            await interaction.response.send_message(
                "Enter a dollar amount such as `50` or `125.00`.",
                ephemeral=True,
            )
            return
        if not 10 <= dollars <= 100_000:
            await interaction.response.send_message(
                "Flip profit must be between $10 and $100,000.",
                ephemeral=True,
            )
            return
        self.owner.flip_min_profit_cents = int(round(dollars * 100))
        await self.owner._refresh(
            interaction,
            status=(
                "📈 Draft Flip Override minimum set to "
                f"**${self.owner.flip_min_profit_cents / 100:,.2f}**."
            ),
        )
