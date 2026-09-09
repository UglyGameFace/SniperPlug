# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the existing July 31 permanent panel stays useful across deployments; movie offers are current, actionable, poster-aware, optionally local to a user's ZIP/radius, and resilient when one source fails. Atom degradation must not poison the rest of the movie surface.

## Scope
`public source discovery → fail-closed parse → per-source health/backoff/fallback → durable freshness-qualified cache → poster enrichment → actionable panel/latest/status → obvious result actions → optional per-user ZIP/radius local view → configured alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**#265, #266, and #267 are merged and exact-head validated. Production acceptance proved Atom indexed recovery is still incomplete. #268 is the single active draft for the remaining Atom failure.**

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
- #268 branch: `fix/atom-multi-index-fallback-268`
- #269 was closed unmerged as superseded after its Bing-wrapper work was integrated into #268.

## Root cause / findings
Direct Atom promotion-page requests from production still return HTTP 403.

Indexed recovery evolved in stages:
- #263 used Bing `url:` against one exact canonical `/promotions` URL and failed when Bing omitted that hub.
- #266 switched to `site:www.atomtickets.com/promotions` and allowed the canonical hub or one-level official `/promotions/<slug>` pages.
- Production after #266 still reported `Bing did not return an allowlisted official Atom promotions URL` even while current public search exposed the live Atom hub and official promotion detail pages.

Two retrieval-layer causes now matter:
1. **Bing result wrapping.** Current Bing results can expose `https://www.bing.com/ck/a?...&u=a1<url-safe-base64>...` instead of the destination URL. #266 validated the raw result hostname first, so a legitimate Atom result wrapped by Bing was rejected before its encoded destination was examined.
2. **Bing omission as a single-point failure.** Even after wrapper handling, Bing may legitimately omit Atom or rate-limit. A second bounded public index is needed before declaring indexed recovery unavailable.

The safe architecture is therefore one authoritative recovery path: direct Atom first; on explicit 401/403/429, Bing RSS first with strict wrapper decoding; if Bing still has no allowlisted Atom result, bounded DuckDuckGo no-JS search; every decoded/returned target is re-run through the same strict Atom HTTPS host/path allowlist. Search snippets never create codes.

## Existing merged behavior that must remain intact
### #265
- Optional per-user ZIP, exact TEXT Discord user ID, radius 10/25/50/100 miles.
- Set/Change ZIP, Use Local, Show All, Clear ZIP.
- Conservative SeeItEarly locality proof from explicitly Free city-page entries only.
- Distance-unverified screenings hidden in Local mode; broad discovery remains available.
- Result buttons include actual movie titles.

### #266
- Direct Atom authoritative with six-hour reprobe while blocked.
- Indexed fallback only after Atom 401/403/429.
- Strict HTTPS `atomtickets.com` / `www.atomtickets.com` allowlist.
- Only `/promotions` or one-level `/promotions/<slug>` qualify.
- Search snippets yield zero reusable codes.
- Persistent 30-minute indexed refresh / six-hour direct reprobe markers.

### #267
- Permanent-panel Check Now acknowledges at the component boundary before delegated work.
- No double-defer for already acknowledged interactions.

## #268 implementation
### Bing first
- Preserve Bing RSS as the first indexed source.
- Accept direct allowlisted Atom result URLs unchanged.
- Also accept only Bing's exact HTTPS `bing.com` / `www.bing.com` `/ck/a` result wrapper.
- Require exactly one non-empty `u=` value with the current `a1` prefix and a bounded URL-safe-base64 payload.
- Decode that destination locally; never follow the Bing tracker.
- Re-run the decoded destination through the #266 Atom promotions URL normalizer.
- Wrapped off-domain, HTTP, wrong-path, nested-promotion, malformed-base64, non-Bing-host, and wrong-Bing-path results fail closed.

### DuckDuckGo second
- If Bing returns no allowlisted Atom result, query DuckDuckGo's public no-JS HTML surface with the same bounded Atom directory query.
- Parse only `result__a` result-title anchors.
- Decode DuckDuckGo direct, protocol-relative, and relative `/l/?...&uddg=...` result redirects only through the explicit `uddg` target.
- Re-run every target through the same Atom normalizer and identity checks.
- Prefer `/promotions`; otherwise accept the first one-level detail page.

### Preserved safety/runtime behavior
- Direct Atom remains first and all cooldown/reprobe behavior remains unchanged.
- Indexed fallback synthetic HTML remains zero-code and clears stale public Atom-code history rather than inferring from snippets.
- Provider-specific diagnostics are returned when both public indexes fail.
- No CAPTCHA evasion, proxy rotation, browser impersonation, login scraping, gated-code guessing, or private invitation scraping.

## Validation / results
### #265
Ubuntu/PRoot Python 3.12 + native libsql: targeted passed, pip check clean, compileall 0, import smoke 37/22, native libsql 10 passed, full pytest **1,353 passed, 1 warning**, diff/worktree clean.

### #266
Exact head `b03fe817dfb70395a40082c35c31d74259b40683`: targeted passed, pip check clean, compileall 0, import smoke 37/22, native libsql 10 passed, full pytest **1,359 passed, 1 warning**, diff/worktree clean.

### #267
Exact head `a7de3231f7089b0e956f99cb7e2f2d97dcf6db37`: targeted passed, pip check clean, compileall 0, import smoke 37/22, native libsql 10 passed, full pytest **1,362 passed, 1 warning**, diff/worktree clean.

### #268
**Not yet validated.** Regression coverage now includes:
- Bing `ck/a` wrapped Atom destination decoding and strict rejection cases;
- wrapped canonical-hub preference;
- DuckDuckGo redirect decoding and strict result-anchor/Atom identity checks;
- exact production shape where Bing has no usable Atom result and DuckDuckGo succeeds;
- stopping after Bing success;
- combined provider diagnostics when both indexes fail;
- zero-code fallback semantics.

The lone known warning in prior runs is discord.py's existing `audioop` deprecation warning. GitHub-hosted Actions have repeatedly terminated before checkout with no executable steps; such red checks are not executed test failures.

## Production acceptance
After #268 is validated, merged, and deployed:
1. Direct Atom may still be HTTP 403.
2. A valid Bing result must work whether its Atom destination is direct or wrapped in `bing.com/ck/a`.
3. If Bing still has no valid Atom result but DuckDuckGo exposes one, Atom source health must become `Indexed fallback current` rather than `Stale/unavailable`.
4. No reusable promo code may be inferred from either index.
5. A future successful direct Atom reprobe must immediately restore direct authority.
6. Check Now, ZIP/local filtering, Show All, posters, title-labeled result buttons, Fandango, Gofobo, SeeItEarly, alert delivery, freshness, dedupe, and feedback must remain intact.

Do not call the movie-ticket task restored until #268 Atom production acceptance passes.

## Risks / boundaries
- Public indexes can independently omit results or rate-limit; #268 removes Bing as a single point of failure but still fails closed if neither index confirms Atom.
- DuckDuckGo HTML is a public no-JS search surface, not a formal paid API, so parser drift/rate-limiting remains possible and is surfaced explicitly.
- Indexed confirmation proves only that a public index exposes an allowlisted official Atom promotions URL. It does not prove a reusable code or remaining inventory.

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
Freeze the current #268 head, run targeted wrapper + multi-index + affected movie tests, pip check, compileall, import smoke, native libsql, full pytest, and diff check in the known-good Ubuntu/PRoot Python 3.12 environment. If green, merge that exact SHA, deploy latest `main`, then re-open Status on the existing July 31 panel and confirm Atom indexed recovery succeeds.