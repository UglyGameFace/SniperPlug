# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the existing July 31 permanent movie panel remains useful across deployments; movie offers are current, clearly actionable, poster-aware, optionally local to a user's ZIP/radius, and resilient when one source fails. Atom degradation must not poison the rest of the movie surface.

## Scope
`public source discovery → fail-closed parse → per-source health/backoff/fallback → durable freshness-qualified cache → poster enrichment → actionable panel/latest/status → obvious result actions → optional per-user ZIP/radius local view → configured alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**#265 merged. Movie ZIP/local browsing is code-complete and validated; active work remains production acceptance plus Atom fallback v2 because direct Atom still returns HTTP 403 and the exact-canonical Bing fallback is too narrow.**

Movie milestones:
- #260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
- #261 merge: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`
- #262 merge: `00335c0943c6b9fc214329c8468b35049ac9b23b`
- #263 merge: `85e26ffa76a8a76fe6aac32c98393e2cbd5c54ee`
- #264 merge: `6e745529a13d881a9e651d2cc6dfd221cb7ef4e1`
- #265 validated head: `69ab983b82032e826a90f9dcb741477bf907685f`
- #265 merge: `d4d22c704c4a38c7a094f2d09f553583c6a6f3d7`

## Root causes / findings
1. The original movie surface treated stale source state as useful too long and had poor recovery UX.
2. Atom's public promotions page is reachable publicly but the hosted SniperPlug direct request receives HTTP 403.
3. The first Atom indexed fallback required Bing to return the exact canonical `/promotions` URL. Production showed that this exact-match requirement can fail even when official Atom promotion pages are indexed.
4. Manual movie results previously relied on users discovering clickable embed titles. #264 added explicit action buttons, and #265 labels those actions with actual movie titles.
5. SeeItEarly can expose screenings far enough away to be impractical. #265 adds optional per-user ZIP/radius local browsing and fails closed on locality proof.
6. #265 review found Discord interaction timing risks: pre-modal Turso reads and pre-acknowledgement network work could cause generic interaction failures. Those paths were hardened before merge.

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

## Validation / results
Exact #265 head `69ab983b82032e826a90f9dcb741477bf907685f` was executed in Ubuntu/PRoot with Python 3.12 and native libsql:
- targeted #265 + affected movie tests: passed
- `python -m pip check`: clean
- `python -m compileall -q sniperplug scripts tests`: exit 0
- import smoke: 37 modules / 22 symbols
- native libsql process tests: 10 passed
- full pytest: **1,353 passed, 1 warning**
- `git diff origin/main...HEAD --check`: clean
- worktree status: clean
- final HEAD matched the exact validated SHA

The lone warning is the existing discord.py `audioop` deprecation warning. GitHub-hosted Actions remained red because jobs terminated before checkout with no executable steps; they were not executed test failures.

## Production acceptance after #265 deploy
1. Manual result views visibly show Set/Change ZIP, Use Local, Show All, and Clear ZIP.
2. Set/Change ZIP opens promptly even if Turso is slow.
3. Use Local acknowledges promptly before DB/source work.
4. Users can browse without ever setting ZIP.
5. ZIP `06484` with radius 25 resolves to Shelton, CT and does not present ~60-mile New York screenings as local.
6. Only explicitly free SeeItEarly city entries may satisfy Local mode.
7. Show All restores broader discovery without erasing the saved preference.
8. Clear ZIP removes only that user's preference.
9. Another user's browsing and guild alert destination remain unchanged.
10. Posters, title-labeled result buttons, source health, freshness, dedupe, and feedback remain intact.

## Atom fallback v2 — active next implementation
Preserve direct Atom as authoritative. On explicit Atom access-block statuses only (401/403/429), broaden indexed confirmation from exact canonical `/promotions` matching to strictly allowlisted official Atom promotion URLs:
- HTTPS only;
- host must be `atomtickets.com` / approved Atom host;
- path must be `/promotions` or `/promotions/<slug>`;
- never infer reusable codes from search snippets;
- never accept off-domain or unrelated Atom paths;
- preserve persisted direct-block and indexed-confirmation cooldowns;
- direct Atom must automatically regain authority when a scheduled reprobe succeeds;
- source health must distinguish `direct blocked / indexed official pages available` from true direct health.

## Risks / boundaries
- Local mode is intentionally conservative and not exhaustive at wide radii because the nearby postal source is bounded.
- Failure to confirm locality hides a screening from Local mode; Show All remains the escape hatch.
- Atom indexed fallback is confirmation/discovery only, not permission to bypass access controls or infer private/gated promo codes.
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
Deploy merged #265 for live acceptance, then implement Atom fallback v2 on fresh `main`, validate exact-head targeted/full tests, and deploy. Do not call the movie-ticket task restored until both #265 production acceptance and Atom source recovery behavior pass live checks.
