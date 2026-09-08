# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the existing July 31 permanent movie panel remains useful across deployments; movie offers are current, clearly actionable, poster-aware, optionally local to a user's ZIP/radius, and resilient when one source fails. Atom degradation must not poison the rest of the movie surface.

## Scope
`public source discovery → fail-closed parse → per-source health/backoff/fallback → durable freshness-qualified cache → poster enrichment → actionable panel/latest/status → obvious result actions → optional per-user ZIP/radius local view → configured alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**#265, #266, and #267 are merged and exact-head validated. Production acceptance proved Atom recovery is still incomplete. #268 is open as the active draft implementation slice.**

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
- #267 validated head: `a7de3231f7089b0e956f99cb7e2f2d97dcf6db37`
- #267 merge: `6feaa14ac6bf327a57000adf9e20f76542d9cbd6`

## Root cause / findings
Direct Atom promotion-page requests from production still return HTTP 403.

Earlier indexed recovery evolved in two steps:
- #263 used Bing `url:` against one exact canonical `/promotions` URL and failed when Bing omitted that exact hub.
- #266 switched to a strict `site:www.atomtickets.com/promotions` directory query and allowed the canonical hub or exactly one-level official `/promotions/<slug>` pages.

Production after #266 still reports:
`Official Atom promotions page returned HTTP 403; indexed fallback also failed: Bing did not return an allowlisted official Atom promotions URL.`

This proves the remaining failure is no longer the Atom URL matcher. **Bing RSS itself is an unreliable single discovery dependency from the production host.** Public web research on 2026-09-08 can see the live Atom promotions hub and multiple official promotion-detail pages while SniperPlug's Bing RSS response exposes none.

Atom's published terms grant public search engines permission to index its public pages, so #268 consumes public search-index results rather than bypassing Atom's direct HTTP access control. Search snippets remain non-authoritative and can never create reusable promo codes.

## Existing merged behavior that must remain intact
### #265
- Optional per-user ZIP stored with exact TEXT Discord user ID.
- Radius choices 10 / 25 / 50 / 100 miles, default 25.
- Set/Change ZIP, Use Local, Show All, Clear ZIP.
- Zippopotam ZIP/nearby resolution + Haversine bounds.
- SeeItEarly local proof only from explicitly Free entries inside What's playing.
- Distance-unverified screenings hidden in Local mode; broad discovery remains under Show All.
- Result buttons carry actual movie titles.

### #266
- Direct Atom remains authoritative and is reprobed every six hours while blocked.
- Indexed fallback activates only after 401/403/429.
- Strict HTTPS `atomtickets.com` / `www.atomtickets.com` allowlist.
- Only `/promotions` or one-level `/promotions/<slug>` URLs qualify.
- Search snippets produce zero reusable codes.
- 30-minute indexed refresh / six-hour direct reprobe markers remain persistent.

### #267
- Permanent-panel Check Now acknowledges at the Discord component boundary before delegated work.
- No double-defer for already acknowledged interactions.

## #268 implementation
- Preserve Bing RSS as the first indexed confirmation source.
- If Bing returns no allowlisted Atom result, query DuckDuckGo's public no-JS HTML search surface with the same bounded Atom directory query.
- Parse only DuckDuckGo result-title anchors (`result__a`).
- Decode DuckDuckGo direct, protocol-relative, and common relative `/l/?...&uddg=...` result redirects only through the explicit `uddg` target parameter.
- Re-run every candidate through the same strict Atom promotion URL normalizer used by #266.
- Require Atom + promotion/offer/ticket identity in the result title.
- Prefer the canonical `/promotions` hub when available; otherwise accept the first strictly allowlisted one-level detail page.
- Keep indexed snippets non-authoritative: synthetic fallback HTML still yields zero reusable promo codes and clears stale Atom code history.
- Keep direct Atom first and preserve all cooldown/reprobe behavior.
- Report both index-provider failures when neither index can confirm an official Atom URL.

## Validation / results
### #265
Ubuntu/PRoot Python 3.12 + native libsql: targeted passed, pip check clean, compileall 0, import smoke 37 modules / 22 symbols, native libsql 10 passed, full pytest **1,353 passed, 1 warning**, diff/worktree clean.

### #266
Exact head `b03fe817dfb70395a40082c35c31d74259b40683`: targeted passed, pip check clean, compileall 0, import smoke 37/22, native libsql 10 passed, full pytest **1,359 passed, 1 warning**, diff/worktree clean.

### #267
Exact head `a7de3231f7089b0e956f99cb7e2f2d97dcf6db37`: targeted passed, pip check clean, compileall 0, import smoke 37/22, native libsql 10 passed, full pytest **1,362 passed, 1 warning**, diff/worktree clean.

### #268
**Not yet validated.** New regression coverage includes:
- DuckDuckGo relative and protocol-relative redirect decoding to official Atom promo detail URLs;
- canonical hub preference;
- rejection of off-domain/wrong-path/non-result/non-Atom candidates;
- the exact production shape: Bing 200 with no Atom result followed by successful second-index confirmation;
- stopping after Bing when Bing succeeds;
- combined provider diagnostics when both indexes fail;
- fallback synthetic HTML remains zero-code.

The lone known warning in prior runs is the existing discord.py `audioop` deprecation warning. GitHub-hosted Actions have repeatedly terminated before checkout with no executable steps; such red checks are not treated as executed test failures.

## Production acceptance
After #268 is validated, merged, and deployed:
1. Direct Atom may still be HTTP 403.
2. If Bing RSS omits Atom but DuckDuckGo exposes an allowlisted official Atom promotions URL, Atom source health must become `Indexed fallback current` instead of `Stale/unavailable`.
3. No reusable promo code may be inferred from either search index.
4. A future successful direct Atom reprobe must immediately restore direct authority.
5. Check Now, ZIP/local filtering, Show All, posters, title-labeled result buttons, Fandango, Gofobo, SeeItEarly, alert delivery, freshness, dedupe, and feedback must remain intact.

Do not call the movie-ticket task restored until #268 Atom production acceptance passes.

## Risks / boundaries
- Public search indexes can independently omit results or rate-limit; #268 removes Bing as a single point of failure but still fails closed if neither index confirms Atom.
- DuckDuckGo HTML is a public no-JS search surface, not a formal paid API, so parser breakage/rate-limiting remains possible and is surfaced as provider-specific failure rather than silently accepted.
- Indexed confirmation proves only that a public index currently exposes an allowlisted official Atom promotions URL. It does not prove a reusable code or remaining inventory.
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
Freeze the current #268 head, run targeted + full Python 3.12/native-libsql validation in the known-good Ubuntu/PRoot environment, inspect the final exact-head diff, and only then mark ready/merge.
