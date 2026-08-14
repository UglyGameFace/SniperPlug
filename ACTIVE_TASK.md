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

if 'def build_compact_deal_card_embed(' in text:
    raise SystemExit('phase2 already applied')

text = text.replace('from sniperplug.services.deal_finder_telemetry import top_route_lines\n', '')
text = text.replace(
    'embeds=[card.embed for card in ranked.verified]',
    'embeds=[build_compact_deal_card_embed(card, verified=True) for card in ranked.verified]',
)
text = text.replace(
    'embeds=[card.embed for card in ranked.review]',
    'embeds=[build_compact_deal_card_embed(card, verified=False) for card in ranked.review]',
)
text = text.replace(
    'await interaction.followup.send(embed=sanitize_embed(card.embed), ephemeral=True)',
    'await interaction.followup.send(embed=build_compact_deal_card_embed(card, verified=True), ephemeral=True)',
)
text = text.replace(
    'embeds=[sanitize_embed(card.embed) for card in batch],',
    'embeds=[build_compact_deal_card_embed(card, verified=False) for card in batch],',
)

menu_start = text.index('def build_deal_mode_menu_embed(')
summary_start = text.index('def build_deal_finder_summary(', menu_start)
compact_block = '''def build_deal_mode_menu_embed(
    query: str,
    ranked: ModeRankedCards,
    freshness: ScanFreshness | None = None,
) -> discord.Embed:
    mode = ranked.mode
    shown = len(ranked.verified) + len(ranked.review)
    changes = ""
    if freshness:
        changes = f" • 🆕 {freshness.new_count} new • 📉 {freshness.price_drop_count} drops"
    embed = discord.Embed(
        title=f"🎛️ Results • {mode.display_name}",
        description=(
            f"**{query}** • **{shown} shown**{changes}\\n"
            f"{mode.emoji} **{mode.label}:** {mode.description}\\n\\n"
            "Choose another mode below. **Fresh Scan** checks Walmart live again; the other modes reuse this scan."
        ),
        color=discord.Color.blurple() if mode.public_safe else discord.Color.dark_gold(),
    )
    if not mode.public_safe:
        embed.add_field(
            name="🟨 Private mode",
            value="Hidden Gems can include review/search leads. They never become public deal proof by themselves.",
            inline=False,
        )
    embed.set_footer(text="The selected mode is highlighted and disabled. One control panel stays current.")
    return embed


def _compact_lines(
    value: str,
    keywords: tuple[str, ...],
    *,
    limit: int = 7,
    fallback_to_all: bool = True,
) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    selected = [line for line in lines if any(keyword in line.lower() for keyword in keywords)]
    if not selected and fallback_to_all:
        selected = lines
    return "\\n".join(selected[:limit])[:1024] or "—"


def _source_field(embed: discord.Embed, *needles: str):
    for field in embed.fields:
        name = str(field.name or "").lower()
        if any(needle in name for needle in needles):
            return field
    return None


def _compact_product_title(source_title: str | None, fallback: str) -> str:
    title = " ".join(str(source_title or fallback or "Walmart item").split())
    if " • " in title:
        prefix, product = title.split(" • ", 1)
        prefix_key = prefix.lower()
        if any(
            marker in prefix_key
            for marker in ("api-derived", "search match", "review candidate", "flip/value", "verified")
        ):
            title = product
    if len(title) > 90:
        title = title[:89].rstrip() + "…"
    return title


def build_compact_deal_card_embed(card: DealCard, *, verified: bool) -> discord.Embed:
    source = sanitize_embed(card.embed)
    discount = float(getattr(card, "discount", 0) or 0)
    product_title = _compact_product_title(source.title, card.label)
    title_prefix = "🔥" if verified else "🟨"
    embed = discord.Embed(
        title=f"{title_prefix} {product_title}",
        url=source.url or card.url,
        color=source.color,
    )
    thumbnail_url = getattr(source.thumbnail, "url", None)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    if verified:
        embed.description = "✅ **Verified Walmart deal**" + (
            f" • **{discount:.0f}% off**" if discount > 0 else ""
        )
    else:
        embed.description = "**Private shopping lead** • useful price/review data, not public markdown proof."

    price = _source_field(source, "price")
    if price is not None:
        embed.add_field(
            name="💰 Price",
            value=_compact_lines(
                str(price.value),
                (
                    "item price",
                    "current",
                    "delivered",
                    "shipping",
                    "was/typical",
                    "savings",
                    "markdown",
                    "coupon",
                    "walmart cash",
                    "reference",
                ),
            ),
            inline=False,
        )

    detail_field = _source_field(source, "api fields")
    seller = _source_field(source, "seller", "offer") or detail_field
    if seller is not None:
        seller_value = _compact_lines(
            str(seller.value),
            ("seller", "condition", "marketplace", "offer type"),
            limit=4,
            fallback_to_all=False,
        )
        if seller_value != "—":
            embed.add_field(name="🏪 Seller", value=seller_value, inline=False)

    delivery = _source_field(source, "fulfillment", "stock") or detail_field
    if delivery is not None:
        delivery_value = _compact_lines(
            str(delivery.value),
            ("stock", "add-to-cart", "fulfillment", "shipping", "delivered", "available online"),
            limit=5,
            fallback_to_all=False,
        )
        if delivery_value != "—":
            embed.add_field(name="📦 Delivery", value=delivery_value, inline=False)

    if not verified:
        comp = _source_field(source, "marketplace comp", "flip context")
        if comp is not None:
            comp_value = _compact_lines(
                str(comp.value),
                ("comp", "spread", "margin", "price"),
                limit=4,
                fallback_to_all=False,
            )
            if comp_value != "—":
                embed.add_field(name="📈 Review context", value=comp_value, inline=False)

    links = _source_field(source, "links")
    if links is not None:
        embed.add_field(name="🔗 Open product", value=str(links.value)[:1024], inline=False)
    else:
        link_block = deal_scanner.product_link_block(
            getattr(card, "link_choices", ()),
            fallback_url=card.url,
        )
        if link_block:
            embed.add_field(name="🔗 Open product", value=link_block, inline=False)

    embed.set_footer(
        text=(
            "Walmart price and stock can change quickly."
            if verified
            else "Private lead only • verify before sharing publicly."
        )
    )
    return sanitize_embed(embed)


'''
text = text[:menu_start] + compact_block + text[summary_start:]

summary_start = text.index('def build_deal_finder_summary(')
new_summary = '''def build_deal_finder_summary(
    result: DealFinderResult,
    *,
    ranked: ModeRankedCards | None = None,
    freshness: ScanFreshness | None = None,
) -> discord.Embed:
    review_count = len(result.review_candidates.cards)
    verified_count = len(result.verified_cards)
    mode = ranked.mode if ranked else DEAL_SEARCH_MODES[MODE_BEST]
    shown = (len(ranked.verified) + len(ranked.review)) if ranked else verified_count
    cache_hits = getattr(result, "cache_hits", 0)

    if verified_count:
        result_line = f"✅ **{verified_count} verified deal(s)** at **{result.min_discount}%+**"
    elif review_count:
        result_line = (
            f"🟨 No verified **{result.min_discount}%+** markdown yet • showing private shopping leads"
        )
    else:
        result_line = f"No verified **{result.min_discount}%+** markdown found in this pass"

    embed = discord.Embed(
        title=f"🔌 {mode.display_name} • {result.query}",
        description=(
            f"{result_line}\\n"
            f"**{shown} shown** • **{result.products_checked} products checked** • "
            f"**{result.searches_attempted} live routes**"
        ),
        color=(
            discord.Color.red()
            if verified_count
            else discord.Color.dark_gold()
            if review_count
            else discord.Color.orange()
        ),
    )
    embed.add_field(name=f"{mode.emoji} {mode.label}", value=mode.description, inline=False)

    if freshness:
        embed.add_field(
            name="🆕 Since the previous scan",
            value=(
                f"**{freshness.new_count} new** • **{freshness.price_drop_count} price drops** • "
                f"**{freshness.repeat_count} repeats**"
            ),
            inline=False,
        )
    if getattr(result, "force_refresh", False):
        embed.add_field(
            name="🔄 Fresh Scan",
            value="Short route cache bypassed; Walmart was checked live again.",
            inline=False,
        )
    if ranked and not ranked.mode.public_safe:
        embed.add_field(
            name="🟨 Private results",
            value="These leads stay private unless they later pass the normal verified-deal gates.",
            inline=False,
        )
    if result.warnings:
        embed.add_field(
            name="⚠️ Walmart notes",
            value="\\n".join(f"• {warning}" for warning in result.warnings[:2]),
            inline=False,
        )

    embed.set_footer(
        text=(
            f"Cache hits: {cache_hits} • Change starting markdown with /deal_threshold • "
            "Fresh Scan spends another live pass."
        )
    )
    return sanitize_embed(embed)
'''
text = text[:summary_start] + new_summary

path.write_text(text)
<!-- DEALS_PATCH_END -->