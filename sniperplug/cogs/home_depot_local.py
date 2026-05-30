from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanRequest, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.home_depot_product_lookup import HomeDepotProductDetail, fetch_home_depot_product_detail
from sniperplug.services.penny_score import score_penny_candidate
from sniperplug.services.quota_guard import serpapi_quota_guard
from sniperplug.cogs.home_depot_search import home_depot_link_block, money, price_ending, trim_title


ZIP_RE = re.compile(r"^\d{5}$")
SKU_RE = re.compile(r"^[A-Za-z0-9-]{4,24}$")
ID_TOKEN_RE = re.compile(r"[A-Za-z0-9-]{4,}")


@dataclass(frozen=True)
class HomeDepotLocalScan:
    sku: str
    zip_code: str
    query: str
    candidates: tuple[SourceCandidate, ...]
    warnings: tuple[str, ...]
    quota_text: str
    returned_count: int = 0
    returned_candidates: tuple[SourceCandidate, ...] = ()
    match_mode: str = "blocked"
    match_note: str = ""
    detail: HomeDepotProductDetail | None = None
    detail_lookup_used: bool = False

    @property
    def best_candidate(self) -> SourceCandidate | None:
        return self.candidates[0] if self.candidates else None


class HomeDepotLocalCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="hd_stock", description="Check a Home Depot SKU near a ZIP and show local stock/price proof candidates.")
    @app_commands.describe(
        sku="Home Depot SKU / Internet #, like 334851114.",
        zip_code="5-digit ZIP code to anchor the local Home Depot check.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hd_stock(self, interaction: discord.Interaction, sku: str, zip_code: str) -> None:
        await interaction.response.defer(ephemeral=True)
        cleaned_sku, cleaned_zip = _clean_sku(sku), _clean_zip(zip_code)
        error = _validation_error(cleaned_sku, cleaned_zip)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return

        scan = await run_home_depot_local_scan(interaction.user.id, cleaned_sku, cleaned_zip)
        await interaction.followup.send(embed=build_hd_stock_embed(scan), ephemeral=True)

    @app_commands.command(name="hd_penny_zip", description="Start a ZIP-anchored Home Depot penny/clearance scan using a safe default search.")
    @app_commands.describe(zip_code="5-digit ZIP code to anchor the penny/clearance scan.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hd_penny_zip(self, interaction: discord.Interaction, zip_code: str) -> None:
        await interaction.response.defer(ephemeral=True)
        cleaned_zip = _clean_zip(zip_code)
        if not ZIP_RE.match(cleaned_zip):
            await interaction.followup.send("Please enter a valid 5-digit ZIP code.", ephemeral=True)
            return

        provider = provider_registry.get("home_depot_serpapi")
        if provider is None:
            await interaction.followup.send("Home Depot SerpApi provider is not registered yet.", ephemeral=True)
            return
        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send(health.message, ephemeral=True)
            return

        quota = serpapi_quota_guard.check(interaction.user.id, cost=1)
        if not quota.allowed:
            await interaction.followup.send(f"SerpApi scan blocked: {quota.reason}", ephemeral=True)
            return

        result = await provider.scan(
            ProviderScanRequest(
                source_key="home_depot_serpapi",
                query="clearance",
                max_results=24,
                metadata={"zip_code": cleaned_zip, "requested_by": str(interaction.user.id), "scan_type": "hd_penny_zip"},
            )
        )
        quota_after = serpapi_quota_guard.record(interaction.user.id, cost=1)
        candidates = tuple(sorted(result.candidates, key=_penny_sort_key, reverse=True))[:8]
        embed = build_hd_penny_zip_embed(cleaned_zip, candidates, result.warnings, quota_after.daily_used, quota_after.daily_limit)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def run_home_depot_local_scan(user_id: int, sku: str, zip_code: str) -> HomeDepotLocalScan:
    provider = provider_registry.get("home_depot_serpapi")
    if provider is None:
        return HomeDepotLocalScan(sku, zip_code, sku, (), ("Home Depot SerpApi provider is not registered yet.",), "SerpApi unavailable")

    health = await provider.healthcheck()
    if health.status != ProviderStatus.READY:
        return HomeDepotLocalScan(sku, zip_code, sku, (), (health.message,), "SerpApi unavailable")

    quota = serpapi_quota_guard.check(user_id, cost=2)
    if not quota.allowed:
        return HomeDepotLocalScan(sku, zip_code, sku, (), (f"SerpApi 2-pass scan blocked: {quota.reason}",), "SerpApi blocked")

    result = await provider.scan(
        ProviderScanRequest(
            source_key="home_depot_serpapi",
            query=sku,
            max_results=24,
            metadata={"zip_code": zip_code, "requested_by": str(user_id), "scan_type": "hd_stock"},
        )
    )

    exact_candidates = _exact_sku_candidates(result.candidates, sku)
    candidates = exact_candidates
    match_mode = "exact_search_match"
    match_note = "Search proof: requested value matched returned product ID/SKU/UPC/URL token."

    if not candidates:
        candidates = _single_result_candidates(result.candidates, sku)
        if candidates:
            match_mode = "single_search_result"
            match_note = "Search fallback: Home Depot search returned exactly one product for the requested value. Detail lookup still required."
        else:
            quota_after = serpapi_quota_guard.record(user_id, cost=1)
            return HomeDepotLocalScan(
                sku,
                zip_code,
                sku,
                (),
                result.warnings,
                f"SerpApi used: {quota_after.daily_used}/{quota_after.daily_limit} today",
                returned_count=len(result.candidates),
                returned_candidates=tuple(result.candidates),
                match_mode="blocked",
                match_note="Blocked: no exact ID proof and no single-result fallback.",
            )

    candidate = candidates[0]
    detail = None
    detail_lookup_used = False
    product_id = candidate.product_id or candidate.sku
    if product_id:
        detail_lookup_used = True
        detail = await fetch_home_depot_product_detail(product_id, zip_code=zip_code)
        candidates = (_merge_detail_candidate(candidate, detail),)
        match_mode, match_note = _detail_match_mode(sku, candidate, detail, prior_mode=match_mode)

    quota_after = serpapi_quota_guard.record(user_id, cost=2 if detail_lookup_used else 1)
    warnings = tuple(result.warnings) + tuple(detail.warnings if detail else ())
    return HomeDepotLocalScan(
        sku,
        zip_code,
        sku,
        candidates,
        warnings,
        f"SerpApi used: {quota_after.daily_used}/{quota_after.daily_limit} today",
        returned_count=len(result.candidates),
        returned_candidates=tuple(result.candidates),
        match_mode=match_mode,
        match_note=match_note,
        detail=detail,
        detail_lookup_used=detail_lookup_used,
    )


def build_hd_stock_embed(scan: HomeDepotLocalScan) -> discord.Embed:
    candidate = scan.best_candidate
    embed = discord.Embed(
        title=f"🏚️ Home Depot Local Stock Check • SKU {scan.sku}",
        description=f"ZIP: `{scan.zip_code}` • Mode: `{scan.match_mode}`",
        color=_stock_color(scan),
    )

    if not candidate:
        embed.add_field(
            name="No usable stock result returned",
            value=(
                f"Home Depot search returned `{scan.returned_count}` product result(s), but none were safe enough to use.\n"
                "SniperPlug blocked the card because multiple/no results would be too easy to misread."
            ),
            inline=False,
        )
        closest = scan.returned_candidates[0] if scan.returned_candidates else None
        if closest:
            thumbnail = _safe_image_url(closest.image_url)
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)
            embed.add_field(name="Closest returned result", value=_trim_field(_candidate_summary(closest, scan.sku)), inline=False)
            embed.add_field(name="Review link", value=home_depot_link_block(closest), inline=False)
        if scan.warnings:
            embed.add_field(name="Provider notes", value=_trim_field("\n".join(f"• {w}" for w in scan.warnings[:5])), inline=False)
        embed.add_field(name="Proof status", value=_trim_field(scan.match_note), inline=False)
        embed.set_footer(text=f"{scan.quota_text} • Blocked because usable proof was missing.")
        return embed

    thumbnail = _safe_image_url(candidate.image_url)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    safe_url = _safe_url(candidate.product_url)
    if safe_url:
        embed.url = safe_url
    embed.add_field(name="Product proof", value=_trim_field(_product_proof_block(scan, candidate)), inline=False)
    embed.add_field(name="Price proof", value=_trim_field(_local_price_block(candidate)), inline=True)
    embed.add_field(name="Local availability", value=_trim_field(_local_stock_block(candidate)), inline=True)
    embed.add_field(name="Links", value=_trim_field(home_depot_link_block(candidate)), inline=False)
    embed.add_field(name="Proof status", value=_trim_field(f"**{_confidence_label(scan)}**\n{scan.match_note}"), inline=False)

    if scan.warnings:
        embed.add_field(name="Provider notes", value=_trim_field("\n".join(f"• {w}" for w in scan.warnings[:5])), inline=False)
    embed.set_footer(text=f"{scan.quota_text} • Product API detail lookup: {'yes' if scan.detail_lookup_used else 'no'} • Verify before driving/posting.")
    return embed


def build_hd_penny_zip_embed(zip_code: str, candidates: tuple[SourceCandidate, ...], warnings: tuple[str, ...], used: int, limit: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"🟡 Home Depot Penny / Clearance ZIP Scan • {zip_code}",
        description=(
            "V1 scans a targeted Home Depot clearance query with ZIP context and ranks returned candidates by penny/clearance signals. "
            "This is **not** a locked ZIP penny database yet."
        ),
        color=discord.Color.gold(),
    )
    if not candidates:
        embed.add_field(name="No candidates", value="No Home Depot products came back for the ZIP scan. Try `/home_depot_penny_hunt` with a tighter query like `faucet`, `vanity`, `ryobi`, or `milwaukee`.", inline=False)
    else:
        lines: list[str] = []
        for idx, candidate in enumerate(candidates[:6], start=1):
            score = score_penny_candidate(candidate, has_store_id=True).score
            lines.append(
                f"**{idx}. {trim_title(candidate.title, 55)}**\n"
                f"Price: **{money(candidate.current_price)}** • Score: `{score}/100` • SKU: `{candidate.sku or candidate.product_id or 'n/a'}`"
            )
        embed.add_field(name="Top candidates", value=_trim_field("\n\n".join(lines)), inline=False)
    if warnings:
        embed.add_field(name="Provider notes", value=_trim_field("\n".join(f"• {w}" for w in warnings[:5])), inline=False)
    embed.set_footer(text=f"SerpApi used: {used}/{limit} today • Verify in store before posting.")
    return embed


def _merge_detail_candidate(candidate: SourceCandidate, detail: HomeDepotProductDetail | None) -> SourceCandidate:
    if detail is None:
        return candidate
    attrs = dict(candidate.variant_attributes or {})
    for key, value in {
        "internet_number": detail.product_id,
        "store_sku_number": detail.store_sku_number,
        "upc": detail.upc,
        "model_number": detail.model_number,
        "brand": detail.brand,
        "rating": detail.rating,
        "reviews": detail.reviews,
        "fulfillment_store": detail.fulfillment_store,
    }.items():
        if value:
            attrs[key] = str(value)
    if detail.fulfillment_quantity is not None:
        attrs["fulfillment_quantity"] = str(detail.fulfillment_quantity)
    if detail.fulfillment_options:
        attrs["fulfillment_options"] = " | ".join(option.label() for option in detail.fulfillment_options[:4])
        for option in detail.fulfillment_options:
            key = re.sub(r"[^a-z0-9]+", "_", option.type.lower()).strip("_") or "option"
            attrs[f"fulfillment_{key}"] = option.label()
            if option.quantity is not None:
                attrs[f"fulfillment_{key}_quantity"] = str(option.quantity)

    return SourceCandidate(
        source_key=candidate.source_key,
        retailer=candidate.retailer,
        title=detail.title or candidate.title,
        product_url=detail.link or candidate.product_url,
        current_price=detail.price if detail.price is not None else candidate.current_price,
        typical_price=detail.original_price if detail.original_price is not None else candidate.typical_price,
        image_url=_safe_image_url(detail.image_url) or _safe_image_url(candidate.image_url),
        product_id=detail.product_id or candidate.product_id,
        product_id_type=candidate.product_id_type,
        sku=detail.store_sku_number or candidate.sku,
        upc=detail.upc or candidate.upc,
        model=detail.model_number or candidate.model,
        variant_attributes=attrs,
        stock_status=_detail_stock_status(detail) or candidate.stock_status,
        can_add_to_cart=candidate.can_add_to_cart,
        signals=["Home Depot Product API detail lookup used"] + list(candidate.signals),
    )


def _detail_match_mode(sku: str, candidate: SourceCandidate, detail: HomeDepotProductDetail | None, *, prior_mode: str) -> tuple[str, str]:
    if detail is None:
        return prior_mode, "Detail lookup was not available; using search-result proof only."
    normalized = _normalize_id(sku)
    ids = {_normalize_id(v) for v in (detail.product_id, detail.store_sku_number, detail.upc, detail.model_number, candidate.product_id, candidate.sku) if v}
    if normalized and normalized in ids:
        if _has_local_stock_detail(detail):
            return "product_api_local_match", "Product API proof: requested value matched returned ID/SKU/UPC/model and local fulfillment data was returned."
        return "product_api_id_match", "Product API proof: requested value matched returned ID/SKU/UPC/model, but local quantity/store proof was limited."
    if prior_mode == "single_search_result":
        return "single_result_with_product_api", "Single search result plus Product API detail lookup. Product ID differs from requested store SKU, so verify before public posting."
    return prior_mode, "Search proof remained stronger than Product API ID proof."


def _has_local_stock_detail(detail: HomeDepotProductDetail | None) -> bool:
    if detail is None:
        return False
    return bool(detail.fulfillment_store or detail.fulfillment_quantity is not None or detail.fulfillment_options)


def _stock_color(scan: HomeDepotLocalScan) -> discord.Color:
    if scan.match_mode == "product_api_local_match":
        return discord.Color.green()
    if scan.match_mode in {"product_api_id_match", "exact_search_match", "single_result_with_product_api"}:
        return discord.Color.orange()
    return discord.Color.dark_orange()


def _confidence_label(scan: HomeDepotLocalScan) -> str:
    if scan.match_mode == "product_api_local_match":
        return "Strong staff-review proof"
    if scan.match_mode == "product_api_id_match":
        return "Product confirmed; local stock limited"
    if scan.match_mode == "single_result_with_product_api":
        return "Single-result match; verify SKU before posting"
    if scan.match_mode == "exact_search_match":
        return "Search-result product match"
    return "Not enough proof"


def _product_proof_block(scan: HomeDepotLocalScan, candidate: SourceCandidate) -> str:
    attrs = candidate.variant_attributes or {}
    lines = [
        f"**{trim_title(candidate.title, 120)}**",
        f"Requested SKU/search: `{scan.sku}`",
        f"Internet #: `{candidate.product_id or attrs.get('internet_number') or 'n/a'}`",
        f"Store SKU: `{attrs.get('store_sku_number') or candidate.sku or 'n/a'}`",
    ]
    if candidate.model:
        lines.append(f"Model: `{candidate.model}`")
    if candidate.upc:
        lines.append(f"UPC: `{candidate.upc}`")
    if attrs.get("brand"):
        lines.append(f"Brand: **{attrs['brand']}**")
    return "\n".join(lines)


def _candidate_summary(candidate: SourceCandidate, requested: str) -> str:
    return (
        f"**{trim_title(candidate.title, 90)}**\n"
        f"Requested: `{requested}`\n"
        f"Returned ID/SKU: `{candidate.sku or candidate.product_id or 'n/a'}`\n"
        f"Price: **{money(candidate.current_price)}**\n"
        f"Stock / fulfillment:\n{_local_stock_block(candidate)}"
    )


def _local_price_block(candidate: SourceCandidate) -> str:
    ending = price_ending(candidate.current_price)
    ending_text = f"\nEnding: **.{ending}**" if ending else ""
    if candidate.typical_price and candidate.current_price and candidate.typical_price > candidate.current_price:
        savings = candidate.typical_price - candidate.current_price
        pct = savings / candidate.typical_price * 100
        return f"Now: **{money(candidate.current_price)}**\nWas: **{money(candidate.typical_price)}**\nSave: **{money(savings)} ({pct:.0f}%)**{ending_text}"
    return f"Now: **{money(candidate.current_price)}**\nWas: **Not returned**{ending_text}"


def _local_stock_block(candidate: SourceCandidate) -> str:
    attrs = candidate.variant_attributes or {}
    lines: list[str] = []
    if candidate.stock_status:
        lines.append(candidate.stock_status)
    for key in (
        "fulfillment_store",
        "fulfillment_quantity",
        "fulfillment_options",
        "fulfillment_pickup",
        "fulfillment_delivery",
        "fulfillment_shipping",
        "store_stock",
        "store_stock_status",
        "pickup",
        "delivery",
        "general_stock",
        "general_stock_status",
        "add_to_cart",
        "buy_online_pay_in_store",
        "check_nearby_stores",
    ):
        value = attrs.get(key)
        if value:
            lines.append(f"{key.replace('_', ' ').title()}: {value}")
    if candidate.can_add_to_cart is True:
        lines.append("Add to cart: yes")
    elif candidate.can_add_to_cart is False:
        lines.append("Add to cart: not confirmed")
    return "\n".join(lines[:8]) if lines else "Local stock not returned by provider"


def _detail_stock_status(detail: HomeDepotProductDetail | None) -> str | None:
    if detail is None:
        return None
    if detail.pickup_quantity is not None and detail.fulfillment_store:
        return f"Pickup quantity: {detail.pickup_quantity} at store {detail.fulfillment_store}"
    if detail.pickup_quantity is not None:
        return f"Pickup quantity: {detail.pickup_quantity}"
    if detail.fulfillment_quantity is not None and detail.fulfillment_store:
        return f"Local fulfillment quantity: {detail.fulfillment_quantity} at store {detail.fulfillment_store}"
    return None


def _exact_sku_candidates(candidates: tuple[SourceCandidate, ...], sku: str) -> tuple[SourceCandidate, ...]:
    normalized = _normalize_id(sku)
    exact = [candidate for candidate in candidates if _is_exact_sku_match(candidate, normalized)]
    return tuple(sorted(exact, key=lambda c: score_penny_candidate(c, has_store_id=True).score, reverse=True))


def _single_result_candidates(candidates: tuple[SourceCandidate, ...], sku: str) -> tuple[SourceCandidate, ...]:
    if not sku.isdigit() or len(candidates) != 1:
        return ()
    return tuple(candidates)


def _is_exact_sku_match(candidate: SourceCandidate, normalized_sku: str) -> bool:
    if not normalized_sku:
        return False
    return normalized_sku in _candidate_match_ids(candidate)


def _candidate_match_ids(candidate: SourceCandidate) -> set[str]:
    ids = {_normalize_id(v) for v in (candidate.sku, candidate.product_id, candidate.upc, candidate.selected_offer_id, candidate.model) if v}
    attrs = candidate.variant_attributes or {}
    for key in ("internet_number", "store_sku_number", "upc", "model_number"):
        if attrs.get(key):
            ids.add(_normalize_id(attrs[key]))
    for token in ID_TOKEN_RE.findall(candidate.product_url or ""):
        normalized = _normalize_id(token)
        if normalized:
            ids.add(normalized)
    return ids


def _safe_url(value: str | None) -> str | None:
    if not value:
        return None
    url = str(value).strip()
    if not url:
        return None
    if url.startswith("//"):
        url = f"https:{url}"
    elif url.startswith("/"):
        url = f"https://www.homedepot.com{url}"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(ch.isspace() for ch in url):
        return None
    return url


def _safe_image_url(value: str | None) -> str | None:
    return _safe_url(value)


def _trim_field(value: str, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _penny_sort_key(candidate: SourceCandidate) -> tuple[int, int]:
    score = score_penny_candidate(candidate, has_store_id=True).score
    price_bonus = 100 - int(candidate.current_price or 99)
    return score, price_bonus


def _clean_sku(value: str) -> str:
    return "".join(value.strip().split())


def _clean_zip(value: str) -> str:
    return value.strip()


def _validation_error(sku: str, zip_code: str) -> str | None:
    if not ZIP_RE.match(zip_code):
        return "Please enter a valid 5-digit ZIP code."
    if not SKU_RE.match(sku):
        return "Please enter a valid Home Depot SKU / Internet # using 4-24 letters or numbers."
    return None


def _normalize_id(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())
