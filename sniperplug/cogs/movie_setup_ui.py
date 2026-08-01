from __future__ import annotations

import logging
from typing import Any

import discord

from sniperplug.services.movie_setup_store import (
    ALLOWED_RADII,
    MovieServerSetup,
    MovieUserLocalProfile,
    normalize_radius,
    normalize_zip,
)
from sniperplug.services.movie_ticket_drops import MovieTicketConfig, clean_text


log = logging.getLogger("sniperplug.movie_setup_ui")
SETUP_COLOR = discord.Color.gold()
SETUP_MAIN_CHANNEL_ID = "movies:setup:main_channel"
SETUP_LOCAL_CHANNEL_ID = "movies:setup:local_channel"
SETUP_OPTIONS_ID = "movies:setup:options"
SETUP_RADIUS_ID = "movies:setup:radius"
MEMBER_SET_ZIP_ID = "movies:member:set_zip"
MEMBER_STATUS_ID = "movies:member:status"
MEMBER_TOGGLE_ID = "movies:member:toggle"
MEMBER_REMOVE_ID = "movies:member:remove"


class MovieMainChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: "MovieServerSetupView") -> None:
        self.setup_view = view
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="1. Choose the main national-ticket alert channel",
            min_values=1,
            max_values=1,
            custom_id=SETUP_MAIN_CHANNEL_ID,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        self.setup_view.setup.alert_channel_id = int(channel.id)
        await self.setup_view.refresh(interaction)


class MovieLocalChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, view: "MovieServerSetupView") -> None:
        self.setup_view = view
        super().__init__(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            placeholder="2. Choose the local-screening channel (optional)",
            min_values=1,
            max_values=1,
            custom_id=SETUP_LOCAL_CHANNEL_ID,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        self.setup_view.setup.local_channel_id = int(channel.id)
        await self.setup_view.refresh(interaction)


class MovieSetupOptionsSelect(discord.ui.Select):
    def __init__(self, view: "MovieServerSetupView") -> None:
        self.setup_view = view
        setup = view.setup
        options = [
            _option("Automatic alerts", "enabled", "Master on/off switch for this server.", "📡", setup.enabled),
            _option("Atom codes", "atom", "Public reusable Atom free-ticket codes.", "⚛️", setup.atom_enabled),
            _option("Fandango offers", "fandango", "Free-ticket and clearly labeled purchase-required offers.", "🎟️", setup.fandango_enabled),
            _option("Gofobo local screenings", "gofobo", "Location-aware advance-screening opportunities.", "🎬", setup.gofobo_enabled),
            _option("DM matching members", "local_dm", "Private local alerts for members who save a ZIP.", "📬", setup.local_dm_enabled),
            _option("Post matching local alerts", "local_channel", "Post server-area matches in the local channel.", "📍", setup.local_channel_enabled),
            _option("Worked / Didn’t Work", "feedback", "Community result buttons under public alerts.", "✅", setup.feedback_enabled),
            _option("Member ZIP self-service", "self_service", "Let members privately set or remove their own ZIP.", "🔐", setup.member_self_service_enabled),
        ]
        super().__init__(
            placeholder="3. Choose exactly what this server wants enabled",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id=SETUP_OPTIONS_ID,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = set(self.values)
        setup = self.setup_view.setup
        setup.enabled = "enabled" in selected
        setup.atom_enabled = "atom" in selected
        setup.fandango_enabled = "fandango" in selected
        setup.gofobo_enabled = "gofobo" in selected
        setup.local_dm_enabled = "local_dm" in selected
        setup.local_channel_enabled = "local_channel" in selected
        setup.feedback_enabled = "feedback" in selected
        setup.member_self_service_enabled = "self_service" in selected
        await self.setup_view.refresh(interaction, rebuild=True)


class MovieRadiusSelect(discord.ui.Select):
    def __init__(self, view: "MovieServerSetupView") -> None:
        self.setup_view = view
        current = normalize_radius(view.setup.default_radius_miles)
        options = [
            discord.SelectOption(
                label=f"{radius} miles",
                value=str(radius),
                description=(
                    "Tight neighborhood match" if radius == 10 else
                    "Balanced nearby-area match" if radius == 25 else
                    "Maximum Gofobo-compatible range"
                ),
                default=radius == current,
            )
            for radius in sorted(ALLOWED_RADII)
        ]
        super().__init__(
            placeholder="4. Pick the default local radius",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=SETUP_RADIUS_ID,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.setup_view.setup.default_radius_miles = normalize_radius(self.values[0])
        await self.setup_view.refresh(interaction, rebuild=True)


class MovieServerZipModal(discord.ui.Modal, title="Set this server’s local movie area"):
    zip_code = discord.ui.TextInput(
        label="US ZIP code",
        placeholder="06606",
        min_length=5,
        max_length=5,
        required=True,
    )

    def __init__(self, setup_view: "MovieServerSetupView") -> None:
        super().__init__(timeout=300)
        self.setup_view = setup_view
        if setup_view.setup.server_zip_code:
            self.zip_code.default = setup_view.setup.server_zip_code

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            place = await self.setup_view.cog.lookup_zip(str(self.zip_code.value))
            setup = self.setup_view.setup
            setup.server_zip_code = place.zip_code
            setup.server_place_name = place.place_name
            setup.server_state_code = place.state_code
            setup.server_latitude = place.latitude
            setup.server_longitude = place.longitude
            await self.setup_view.refresh_message()
            await interaction.followup.send(
                f"Server area set to **{place.short_label}**. Press **Save Settings** on the setup panel to apply it.",
                ephemeral=True,
            )
        except Exception as error:  # noqa: BLE001 - modal must fail visibly.
            await interaction.followup.send(
                f"I could not validate that ZIP: `{clean_text(str(error))[:400]}`",
                ephemeral=True,
            )


class MovieServerSetupView(discord.ui.View):
    def __init__(self, cog: Any, *, owner_id: int, guild_id: int, setup: MovieServerSetup) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = int(owner_id)
        self.guild_id = int(guild_id)
        self.setup = setup
        self.message: discord.InteractionMessage | None = None
        self.rebuild_components()

    def rebuild_components(self) -> None:
        self.clear_items()
        self.add_item(MovieMainChannelSelect(self))
        self.add_item(MovieLocalChannelSelect(self))
        self.add_item(MovieSetupOptionsSelect(self))
        self.add_item(MovieRadiusSelect(self))
        self.add_item(MovieSetServerZipButton(self))
        self.add_item(MovieSaveSettingsButton(self))
        self.add_item(MovieScanNowButton(self))
        self.add_item(MovieTestAlertButton(self))
        self.add_item(MoviePostMemberPanelButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This private setup panel belongs to the manager who opened it. Run `/movies setup` to open your own.",
                ephemeral=True,
            )
            return False
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("This setup panel belongs to another server.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction, *, rebuild: bool = False) -> None:
        if rebuild:
            self.rebuild_components()
        await interaction.response.edit_message(embed=build_movie_setup_embed(self.setup), view=self)

    async def refresh_message(self) -> None:
        if self.message is not None:
            await self.message.edit(embed=build_movie_setup_embed(self.setup), view=self)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        log.error(
            "Movie setup component failed item=%s guild=%s user=%s",
            getattr(item, "custom_id", type(item).__name__),
            interaction.guild_id,
            interaction.user.id,
            exc_info=(type(error), error, error.__traceback__),
        )
        if interaction.response.is_done():
            await interaction.followup.send("That setup action failed safely. Please try it again.", ephemeral=True)
        else:
            await interaction.response.send_message("That setup action failed safely. Please try it again.", ephemeral=True)


class MovieSetServerZipButton(discord.ui.Button):
    def __init__(self, setup_view: MovieServerSetupView) -> None:
        self.setup_view = setup_view
        super().__init__(label="Set Server ZIP", emoji="📍", style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MovieServerZipModal(self.setup_view))


class MovieSaveSettingsButton(discord.ui.Button):
    def __init__(self, setup_view: MovieServerSetupView) -> None:
        self.setup_view = setup_view
        super().__init__(label="Save Settings", emoji="💾", style=discord.ButtonStyle.success, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.setup_view.cog.save_setup_from_panel(interaction, self.setup_view.setup)
            await self.setup_view.refresh_message()
            state = "enabled" if self.setup_view.setup.enabled else "disabled"
            await interaction.followup.send(f"Movie-ticket automation is **{state}** with these exact settings.", ephemeral=True)
        except Exception as error:  # noqa: BLE001
            await interaction.followup.send(
                f"Settings were not saved: `{clean_text(str(error))[:500]}`",
                ephemeral=True,
            )


class MovieScanNowButton(discord.ui.Button):
    def __init__(self, setup_view: MovieServerSetupView) -> None:
        self.setup_view = setup_view
        super().__init__(label="Scan Now", emoji="🔎", style=discord.ButtonStyle.primary, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            outcome = await self.setup_view.cog._scan_official_source(target_guild_id=self.setup_view.guild_id)  # noqa: SLF001
            await interaction.followup.send(
                f"Scan finished: **{outcome.active_count}** active offers/screenings, "
                f"**{outcome.delivered_count}** new public alert(s).",
                ephemeral=True,
            )
        except Exception as error:  # noqa: BLE001
            await interaction.followup.send(
                f"The scan failed safely: `{clean_text(str(error))[:500]}`",
                ephemeral=True,
            )


class MovieTestAlertButton(discord.ui.Button):
    def __init__(self, setup_view: MovieServerSetupView) -> None:
        self.setup_view = setup_view
        super().__init__(label="Test", emoji="🧪", style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            jump_urls = await self.setup_view.cog.send_setup_test(interaction, self.setup_view.setup)
            await interaction.followup.send(
                "Test alert posted successfully" + (f": {' • '.join(jump_urls)}" if jump_urls else "."),
                ephemeral=True,
            )
        except Exception as error:  # noqa: BLE001
            await interaction.followup.send(f"Test failed: `{clean_text(str(error))[:500]}`", ephemeral=True)


class MoviePostMemberPanelButton(discord.ui.Button):
    def __init__(self, setup_view: MovieServerSetupView) -> None:
        self.setup_view = setup_view
        super().__init__(label="Post ZIP Panel", emoji="👥", style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await self.setup_view.cog.post_member_zip_panel(interaction, self.setup_view.setup)
            await interaction.followup.send(f"Member ZIP panel posted: {message.jump_url}", ephemeral=True)
        except Exception as error:  # noqa: BLE001
            await interaction.followup.send(
                f"The member panel was not posted: `{clean_text(str(error))[:500]}`",
                ephemeral=True,
            )


class MovieMemberZipModal(discord.ui.Modal, title="Your local free-movie alerts"):
    zip_code = discord.ui.TextInput(
        label="Your US ZIP code",
        placeholder="06606",
        min_length=5,
        max_length=5,
        required=True,
    )
    radius = discord.ui.TextInput(
        label="Radius in miles: 10, 25, or 50",
        placeholder="25",
        min_length=2,
        max_length=2,
        required=True,
    )

    def __init__(self, cog: Any, *, guild_id: int, user_id: int, existing: MovieUserLocalProfile | None) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = int(guild_id)
        self.user_id = int(user_id)
        if existing is not None:
            self.zip_code.default = existing.zip_code
            self.radius.default = str(existing.radius_miles)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            cleaned_zip = normalize_zip(str(self.zip_code.value))
            radius = normalize_radius(str(self.radius.value))
            if str(self.radius.value).strip() not in {str(value) for value in ALLOWED_RADII}:
                raise ValueError("Radius must be 10, 25, or 50 miles.")
            place = await self.cog.lookup_zip(cleaned_zip)
            profile = MovieUserLocalProfile(
                guild_id=self.guild_id,
                user_id=self.user_id,
                zip_code=place.zip_code,
                place_name=place.place_name,
                state_code=place.state_code,
                latitude=place.latitude,
                longitude=place.longitude,
                radius_miles=radius,
                enabled=True,
            )
            await self.cog.setup_store.save_user_profile(profile)
            await interaction.followup.send(
                f"✅ Local movie alerts are enabled for **{place.short_label}** within **{radius} miles**. "
                "Your ZIP stays private and can be paused or removed from the panel.",
                ephemeral=True,
            )
        except Exception as error:  # noqa: BLE001
            await interaction.followup.send(
                f"I could not save that location: `{clean_text(str(error))[:500]}`",
                ephemeral=True,
            )


class MovieMemberLocalPanelView(discord.ui.View):
    def __init__(self, cog: Any) -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Set / Update My ZIP",
        emoji="📍",
        style=discord.ButtonStyle.success,
        custom_id=MEMBER_SET_ZIP_ID,
    )
    async def set_zip(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._allowed(interaction):
            return
        existing = await self.cog.setup_store.get_user_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.send_modal(
            MovieMemberZipModal(
                self.cog,
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                existing=existing,
            )
        )

    @discord.ui.button(
        label="My Settings",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id=MEMBER_STATUS_ID,
    )
    async def status(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._allowed(interaction):
            return
        profile = await self.cog.setup_store.get_user_profile(interaction.guild_id, interaction.user.id)
        if profile is None:
            await interaction.response.send_message("You have not saved a local-alert ZIP yet.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_member_profile_embed(profile), ephemeral=True)

    @discord.ui.button(
        label="Pause / Resume",
        emoji="⏯️",
        style=discord.ButtonStyle.secondary,
        custom_id=MEMBER_TOGGLE_ID,
    )
    async def toggle(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._allowed(interaction):
            return
        profile = await self.cog.setup_store.get_user_profile(interaction.guild_id, interaction.user.id)
        if profile is None:
            await interaction.response.send_message("Set your ZIP first.", ephemeral=True)
            return
        profile.enabled = not profile.enabled
        await self.cog.setup_store.save_user_profile(profile)
        await interaction.response.send_message(
            f"Local movie alerts are now **{'enabled' if profile.enabled else 'paused'}** for you in this server.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Remove My ZIP",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id=MEMBER_REMOVE_ID,
    )
    async def remove(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._allowed(interaction):
            return
        await self.cog.setup_store.delete_user_profile(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            "Your saved ZIP and local movie-alert profile were removed from this server.",
            ephemeral=True,
        )

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this panel inside a server.", ephemeral=True)
            return False
        setup = await self.cog.setup_store.get_server_setup(interaction.guild_id)
        if not setup.member_self_service_enabled or not setup.gofobo_enabled:
            await interaction.response.send_message(
                "This server has not enabled member local-screening profiles.",
                ephemeral=True,
            )
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        log.error(
            "Movie member ZIP panel failed item=%s guild=%s user=%s",
            getattr(item, "custom_id", type(item).__name__),
            interaction.guild_id,
            interaction.user.id,
            exc_info=(type(error), error, error.__traceback__),
        )
        if interaction.response.is_done():
            await interaction.followup.send("That local-alert action failed safely. Please try again.", ephemeral=True)
        else:
            await interaction.response.send_message("That local-alert action failed safely. Please try again.", ephemeral=True)


def build_movie_setup_embed(setup: MovieServerSetup) -> discord.Embed:
    embed = discord.Embed(
        title="🎬 Movie Ticket Setup Center",
        description=(
            "Configure the entire movie-ticket system here. Nothing else is required for routine setup.\n\n"
            "Choose channels, select the sources and alert behavior you want, set the local area, then press **Save Settings**."
        ),
        color=SETUP_COLOR,
    )
    sources = [
        label
        for enabled, label in (
            (setup.atom_enabled, "Atom"),
            (setup.fandango_enabled, "Fandango"),
            (setup.gofobo_enabled, "Gofobo"),
        )
        if enabled
    ]
    local_modes = [
        label
        for enabled, label in (
            (setup.local_dm_enabled, "member DMs"),
            (setup.local_channel_enabled, "local channel"),
        )
        if enabled
    ]
    embed.add_field(
        name="Automation",
        value=f"**{'Enabled' if setup.enabled else 'Disabled'}**\nSources: {', '.join(sources) if sources else 'none selected'}",
        inline=True,
    )
    embed.add_field(
        name="Main alert channel",
        value=f"<#{setup.alert_channel_id}>" if setup.alert_channel_id else "Not selected",
        inline=True,
    )
    embed.add_field(
        name="Local alert channel",
        value=f"<#{setup.local_channel_id}>" if setup.local_channel_id else "Not selected",
        inline=True,
    )
    server_area = (
        f"**{setup.server_zip_code} — {setup.server_place_name}, {setup.server_state_code}**"
        if setup.server_zip_code
        else "Not set"
    )
    embed.add_field(
        name="Server local area",
        value=f"{server_area}\nRadius: **{setup.default_radius_miles} miles**",
        inline=False,
    )
    embed.add_field(
        name="Local delivery",
        value=(
            f"{', '.join(local_modes) if local_modes else 'No local delivery selected'}\n"
            f"Member ZIP panel: **{'allowed' if setup.member_self_service_enabled else 'disabled'}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Community feedback",
        value="Worked / Didn’t Work buttons **on**" if setup.feedback_enabled else "Feedback buttons **off**",
        inline=True,
    )
    warnings: list[str] = []
    if setup.enabled and not setup.any_source_enabled:
        warnings.append("Select at least one source before saving enabled automation.")
    if setup.enabled and (setup.atom_enabled or setup.fandango_enabled) and not setup.alert_channel_id:
        warnings.append("Choose a main channel for national ticket codes.")
    if setup.local_channel_enabled and not setup.local_channel_id:
        warnings.append("Choose a local channel or turn off local-channel delivery.")
    if setup.local_channel_enabled and not setup.server_zip_code:
        warnings.append("Set the server ZIP so local-channel alerts can be distance matched.")
    embed.add_field(
        name="Setup check",
        value="✅ Ready to save" if not warnings else "\n".join(f"⚠️ {warning}" for warning in warnings),
        inline=False,
    )
    embed.set_footer(text="ZIP codes are stored privately as text so leading zeroes are preserved.")
    return embed


def build_member_panel_embed(setup: MovieServerSetup) -> discord.Embed:
    embed = discord.Embed(
        title="📍 Local Free Movie Alerts",
        description=(
            "Press **Set / Update My ZIP** once and SniperPlug will privately match official local screening details to your area. "
            "You do not need to memorize any commands."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="What is saved",
        value="Your ZIP, approximate city/state coordinates, chosen radius, and whether alerts are enabled.",
        inline=False,
    )
    embed.add_field(
        name="Privacy",
        value="Your ZIP is never posted publicly. You can pause alerts or remove the saved profile at any time below.",
        inline=False,
    )
    embed.add_field(
        name="Maximum range",
        value=f"10, 25, or 50 miles • Server default: **{setup.default_radius_miles} miles**",
        inline=False,
    )
    embed.set_footer(text="A screening pass can still fill, expire, or require a Gofobo account, and seating is not guaranteed.")
    return embed


def build_member_profile_embed(profile: MovieUserLocalProfile) -> discord.Embed:
    embed = discord.Embed(title="📍 Your Local Movie Alert Settings", color=SETUP_COLOR)
    embed.add_field(name="Area", value=f"{profile.zip_code} — {profile.place_name}, {profile.state_code}", inline=False)
    embed.add_field(name="Radius", value=f"{profile.radius_miles} miles", inline=True)
    embed.add_field(name="Alerts", value="Enabled" if profile.enabled else "Paused", inline=True)
    embed.set_footer(text="Only you can see this response.")
    return embed


def _option(label: str, value: str, description: str, emoji: str, default: bool) -> discord.SelectOption:
    return discord.SelectOption(
        label=label,
        value=value,
        description=description[:100],
        emoji=emoji,
        default=bool(default),
    )
