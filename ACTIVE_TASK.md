# Active Task

## Active task / outcome
**SP-WM-DM-PERSONALIZATION — PR #291 `Personalize Walmart DM product alerts`.**

Outcome: keep existing broad personal deal categories compatible while allowing each subscriber to narrow **Walmart** DMs by specific product type and optional brand, with notification titles that visually distinguish both deal strength and product identity.

Base main: `e8f4a271aa8f595d6d2e3f8eca3cf9d79f90e2df` (PR #290 merge).
Branch: `feat/walmart-dm-personalization`.
PR: #291, open draft and mergeable.

Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

## Previous task closure
`SP-WM-EXACT-RECOVERY-DEBT` is production-accepted. Fresh post-merge logs proved v290 telemetry live, page recovery drained to zero, the page circuit stayed closed without an observed challenge/403/429 regression, exact verification/fanout remained healthy, and PR #289 code-6001 responses normalized to `api_exact_lookup_no_match`. Production acceptance evidence is recorded on PR #290.

## Status
**Implementation and focused cleanup are complete enough for executable validation. Do not mark ready or merge until exact-head validation passes.**

GitHub-hosted workflows remain runner-side non-evidence: current PR jobs fail in roughly two seconds with `steps=[]` and `runner_id=0`, before any repository command executes.

## Requirement / root cause
Existing personal DMs correctly support exact proof, thresholds, broad categories, favorites, mutes, required/excluded keywords, Flip Override, dedupe and daily limits. The missing layer is product intent inside a category.

Examples:
- portable chargers should not mean every mobile accessory;
- cologne/perfume should exclude body spray/deodorant lookalikes;
- Pokémon TCG should target sealed TCG products rather than generic Pokémon merchandise;
- NeeDoh should not mean every toy.

Notification previews also reused the same heat emoji across unrelated products, so several good alerts visually blended together.

## Execution path inspected
- `/dm_deals` in `sniperplug/cogs/dm_deal_alerts.py` owns the personal-alert command and opens `DmDealPreferencesView`.
- Existing `categories_json` safely stores arbitrary structured control tokens; `normalize_categories()` does not cap or drop `interest:` / `brand:` tokens, so no DB migration is required.
- `sniperplug/services/dm_personal_categories.py` owns category/favorite/mute/flip token composition and now preserves product/brand tokens across those edits.
- `sniperplug/services/dm_deal_matching.py` is the canonical live matcher.
- Walmart bulk fanout, legacy Walmart fanout and verified-retailer fanout all import the canonical matcher from `dm_deal_matching.py`; a regression now locks those imports in place.
- Walmart exact cards carry `card.retailer = Walmart...`; Target/HP cards carry their own retailer identity.
- Product/brand filtering and new badges are therefore gated to Walmart cards only. Target/HP retain their existing matching and title behavior.
- Walmart bulk fanout clones the card embed after the personal match decision, so an idempotent Walmart title decoration at the successful-match boundary reaches the DM without a duplicate renderer.

## Matching design
New structured tokens reuse `categories_json`:
- `interest:<specific_product_profile>`
- `brand:<normalized_brand>`

Semantics:
- multiple product interests are OR;
- multiple brands are OR;
- if both groups are configured, a Walmart deal must match one selected product profile **and** one selected brand;
- explicit Walmart product/brand intent is hard and Flip Override cannot bypass it;
- existing maximum price, Walmart Cash-only, required keywords, excluded keywords, exact proof, dedupe and daily limits remain hard;
- broad category favorites/mutes/allowlists remain separate;
- users with no product/brand tokens keep existing behavior.

## Specific product profiles
Initial deliberately narrow profiles include sealed Pokémon TCG products, NeeDoh, portable chargers/power banks, MagSafe battery packs, cologne/perfume/EDP/EDT, LEGO sets, premium Hot Wheels, Funko Pop, gaming controllers, headphones/earbuds, Bluetooth speakers, SSDs, microSD cards, robot vacuums, air fryers, countertop ice makers, coffee makers, power tools, tool batteries, diapers, strollers, car seats, energy drinks, sneakers, smartwatches, tablets, laptops and computer monitors.

Profile exclusions prevent obvious adjacent junk such as Pokémon binders/sleeves/plush, fragrance body spray/deodorant, LEGO accessories, SSD enclosures, stroller accessories and similar lookalikes.

Pokémon matching identifies sealed TCG product form only. It does not claim popularity/profit merely because an item is Pokémon; resale qualification remains the existing strict Flip assessment's responsibility.

## Product-aware notification badges
Walmart personal-DM titles use at most two leading badges:
1. deal strength: `🚨`, `🔥`, `💸`, `💎`, `⚡`, `✅`, or `🔎`;
2. product identity such as `🧴`, `🃏`, `🔋`, `🧸`, `🧱`, `🎮`, `🎧`, `🚗`, `👶`, `🛒`, etc.

Decoration is idempotent so a shared card evaluated for multiple subscribers does not accumulate emojis. HP/Target titles are unchanged.

## Scope
Exactly nine expected net files:
- `ACTIVE_TASK.md`
- `sniperplug/cogs/dm_deal_alerts.py`
- `sniperplug/cogs/dm_deal_preferences_view.py`
- `sniperplug/services/dm_personal_categories.py`
- `sniperplug/services/dm_product_interests.py`
- `sniperplug/services/dm_deal_matching.py`
- `sniperplug/services/dm_deal_badges.py`
- `tests/test_dm_product_interests_personalization.py`
- `tests/test_dm_product_interest_surface_static.py`

## Changes
- Added structured specific-product and arbitrary-brand helpers with no schema migration.
- Added `/dm_deals` typed add/remove controls and a product catalog.
- Added Specific products and Product catalog controls to the existing private personalization menu.
- Existing category/favorite/mute/flip editors preserve product/brand tokens.
- Canonical matcher enforces the new constraints only for Walmart.
- Added product-aware two-badge Walmart DM titles.
- Added dynamic coverage for portable charger + brand conjunction, fragrance exclusions, sealed Pokémon specificity, NeeDoh specificity, token persistence, Walmart-only scope, existing no-filter compatibility and idempotent badges.
- Added static coverage for command/menu wiring, structured-token preservation, Walmart-only gating and all live fanouts importing the canonical matcher.

## Cleanup / conflicts
- No new persistence layer, migration, retry shim or parallel fanout path was added.
- A pre-existing stale `match_dm_deal()` function remains inside `dm_deal_alerts.py`, but repository inspection found no live fanout or test importing it. All live fanouts use `dm_deal_matching.py`, and the new regression explicitly prevents them from drifting back to the stale function.
- Rewriting the large shared persistence module solely to delete unreachable legacy code was judged higher-risk than leaving the dead compatibility symbol guarded and unreferenced in this focused feature PR.
- Public Walmart posting behavior is not intentionally changed.
- PR #273 and PR #200 remain untouched.

## Validation required
Before PR #291 can be called complete/ready:
- targeted new personalization tests;
- existing DM alert/category/favorite/mute/Flip Override/delivery tests;
- Walmart fanout and personalization static tests;
- Target/HP verified-retailer compatibility tests where applicable;
- compileall;
- import smoke;
- pip check;
- full pytest suite;
- `git diff origin/main...HEAD --check`;
- exact nine-file scope;
- conflict-marker scan;
- clean worktree;
- final exact SHA recheck.

## Blocker
GitHub Actions currently fails before allocating a runner (`steps=[]`, `runner_id=0`). Use the known Python 3.12/native-libsql Termux Ubuntu environment for executable exact-head validation if hosted CI remains unavailable.

## Suspended / backlog
- `SP-MOVIE-TICKETS-RECOVERY` / PR #273 remains suspended.
- eBay watcher PR #200 remains unrelated.
- Backlog: SP-SETUP-DELIVERY-UX, SP-DM-DELIVERY-STARVATION, SP-DM-NEW-LOW-ALERT, SP-WM-CASH-PDP-AMOUNT, HP/Target standalone watcher deployment/acceptance, SniperPlug-Site.

## Next step
Run one complete exact-head validation on PR #291. Repair only regressions attributable to this task. Once the exact head is green, record validation in PR metadata without modifying the branch, then review/merge separately.