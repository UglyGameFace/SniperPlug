# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the existing July 31 permanent movie panel remains useful across deployments; movie offers are current, clearly actionable, poster-aware, optionally local to a user's ZIP/radius, and resilient when one source fails. Atom degradation must not poison the rest of the movie surface.

## Scope
`public source discovery → fail-closed parse → per-source health/backoff/fallback → durable freshness-qualified cache → poster enrichment → actionable panel/latest/status → obvious result actions → optional per-user ZIP/radius local view → configured alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**#265 and #266 are merged and exact-head validated. Active phase is production acceptance for optional ZIP/local browsing plus Atom fallback v2.**

Movie milestones:
- #260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
- #261 merge: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`
- #262 merge: `00335c0943c6b9fc214329c8468b35049ac9b23b`
- #263 merge: `85e26ffa76a8a76fe6aac32c98393e2cbd5c54ee`
- #264 merge: `6e745529a13d881a9e651d2cc6dfd221cb7ef4e1`
- #265 validated head: `69ab983b82032e826a90f9dcb741477bf907685f`
- #265 merge: `d4d22c704c4a38c7a094f2d09f553583c6a6f3d7`
- #266 validated head: `b03fe817dfb70395a40082c35c31d74259b40683`
- #266 merge: `28554dd415e382c7f703d2c623e921d13b14cb5a`

## Root cause / findings
Production direct Atom still receives HTTP 403. #263's indexed recovery also failed because it asked Bing to confirm one exact URL using `url:www.atomtickets.com/promotions` and rejected legitimate official detail pages such as `/promotions/<slug>`.

Research confirmed:
- Microsoft's `url:` operator checks one exact URL/domain index identity.
- Microsoft's `site:` operator searches a site or directory tree.
- Atom's canonical promotions hub is publicly live.
- Official Atom `/promotions/<slug>` pages are publicly indexed.

#266 therefore moved indexed confirmation to directory-level search while preserving a strict Atom-only URL allowlist and zero-code fail-closed behavior.

## #265 implementation
- Optional per-user ZIP, stored as exact TEXT Discord user ID.
- Radius choices: 10 / 25 / 50 / 100 miles; default 25.
- `Set / Change ZIP`, `Use Local`, `Show All`, and `Clear ZIP` controls on manual result views.
- Zippopotam.us ZIP/nearby resolution with Haversine distance bounds.
- SeeItEarly city-page proof restricted to explicitly **Free** entries inside **What's playing**.
- Distance-unverified screening leads are hidden in Local mode while national/non-screening promos remain available.
- ZIP validation shares the location cache with the immediate Local view.
- At most 8 nearby city pages considered and at most 4 fetched concurrently.
- Set/Change ZIP opens its modal before Turso work; Use Local defers before DB/network work.
- DB/source/render failures return visible ephemeral errors and remain fail-closed.
- Manual result buttons include the actual movie title within Discord's label limit.
- Guild-wide automatic alert configuration is untouched by personal ZIP settings.

## #266 implementation
- Direct Atom remains authoritative and is still tried first whenever its six-hour reprobe is due.
- Indexed fallback still activates only after explicit Atom access-block responses: 401, 403, or 429.
- Bing query changed from exact `url:` lookup to `site:www.atomtickets.com/promotions ...` directory search.
- Canonical `/promotions` is preferred when present.
- Otherwise exactly one-level official `/promotions/<slug>` URLs are allowed.
- HTTPS plus exact `atomtickets.com` / `www.atomtickets.com` host requirement remains fail-closed.
- HTTP, off-domain, nested promotion paths, encoded-path tricks, unrelated Atom paths, and unsupported Atom subdomains are rejected.
- Query strings and fragments are stripped from accepted indexed URLs.
- Search result title/description must still identify Atom plus promotion/offer/ticket context.
- Indexed snippets never produce reusable promo codes; stale Atom codes are cleared rather than inferred.
- Existing persisted 30-minute index refresh and six-hour direct Atom reprobe timers remain unchanged.
- Source health says an **official Atom promotions URL** was index-confirmed instead of falsely claiming the canonical hub was confirmed.

## Validation / results
### #265
Exact head `69ab983b82032e826a90f9dcb741477bf907685f` on Ubuntu/PRoot Python 3.12 + native libsql:
- targeted movie tests: passed
- pip check clean
- compileall exit 0
- import smoke: 37 modules / 22 symbols
- native libsql: 10 passed
- full pytest: **1,353 passed, 1 warning**
- diff check and worktree clean

### #266
Exact head `b03fe817dfb70395a40082c35c31d74259b40683` on Ubuntu/PRoot Python 3.12 + native libsql:
- targeted Atom v2 + affected movie tests: passed
- pip check clean
- compileall exit 0
- import smoke: 37 modules / 22 symbols
- native libsql: 10 passed
- full pytest: **1,359 passed, 1 warning**
- diff check and worktree clean
- final HEAD matched the exact validated SHA

The lone warning in both runs is the existing discord.py `audioop` deprecation warning. GitHub-hosted Actions remained red because jobs terminated before checkout with no executable steps; they were not executed test failures.

## Production acceptance
After deploying latest `main`:
1. Optional ZIP controls are visible and usable without forcing setup.
2. ZIP `06484` with radius 25 resolves to Shelton, CT and does not present ~60-mile New York screenings as local.
3. Only explicitly free SeeItEarly city entries may satisfy Local mode.
4. Show All restores broader discovery without erasing the saved preference.
5. Clear ZIP removes only that user's preference.
6. Another user's browsing and guild alert destination remain unchanged.
7. Direct Atom may still report HTTP-blocked, but indexed fallback must succeed when Bing returns an allowlisted official Atom promotions URL.
8. Status should read `Indexed fallback current` and `official Atom promotions URL confirmed via public index`, not `Stale/unavailable` solely because Bing omitted the canonical hub URL.
9. No stale public Atom codes may reappear from index snippets.
10. A successful future direct Atom reprobe must immediately restore direct authority and normal direct health.
11. Fandango, Gofobo, SeeItEarly, posters, title-labeled result buttons, alert delivery, freshness, dedupe, and feedback must remain intact.

Do not call the movie-ticket task restored until #265 live locality acceptance and #266 Atom recovery both pass production checks.

## Risks / boundaries
- Local mode is intentionally conservative and not exhaustive at wide radii because the nearby postal source is bounded.
- Indexed fallback proves only that Bing currently exposes an allowlisted official Atom promotions URL; it does not prove a reusable code or remaining promotion inventory.
- No CAPTCHA evasion, proxy rotation, browser impersonation, account/login scraping, gated-code guessing, or private invitation scraping.

## Backlog — not active
- SP-WM-DEAL-DELIVERY-STALL production acceptance
- SP-GATEWAY-EVENT-LOOP-LAG
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Deploy latest `main` and run live production acceptance against the existing July 31 panel, ZIP/local browsing, and Atom source status. Do not resume implementation unless live evidence shows a concrete failure on the merged path.
