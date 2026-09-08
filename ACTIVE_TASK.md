# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the existing July 31 permanent movie panel stays useful across deployments; current free-ticket and screening sources are freshness-qualified and truthfully classified; managers can configure alerts directly from the panel; Atom degradation does not poison the whole movie surface; and movie cards include trustworthy poster artwork when the connected source exposes it.

## Scope
`public movie source fetch/discovery → fail-closed parse → per-source state/backoff/fallback → durable freshness-qualified cache → poster enrichment → actionable permanent panel/latest/status → configured direct-source alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**PR #263 is merged. Implementation and exact-head validation are complete. The only active phase now is live production acceptance on the deployed movie surface.**

Movie milestones:
- #260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
- #261 validated head: `0275ab55f8c2f7f616700aa8c1305430ab610e6f`
- #261 merge: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`
- #262 validated head: `ac9bf854b9286152ada113ac1aabd5e306462e71`
- #262 merge: `00335c0943c6b9fc214329c8468b35049ac9b23b`
- #263 validated head: `52dedfd4813886ce09ee877f19d08d566e233448`
- #263 merge: `85e26ffa76a8a76fe6aac32c98393e2cbd5c54ee`

Previous Walmart delivery work (#256/#257) remains merged; its production acceptance is paused by the explicit movie-task switch.

## Production findings that #263 addressed
1. Atom's canonical promotions page remained publicly live, but the hosted SniperPlug aiohttp request received HTTP 403 and left Atom stale/unavailable.
2. Fandango/Gofobo already exposed artwork paths, but SeeItEarly cards had no dependable poster resolution path.
3. A fallback marker existed during #263 development, but the authoritative fetch path still needed to consume persisted index/direct-block timestamps so it could avoid repeated blocked requests and report truthful source health.

## Authoritative execution path
`registered_multi_source_movies.MovieTicketsCog`
→ `_scan_official_source`
→ `_scan_atom_source` / Fandango / Gofobo / SeeItEarly
→ `AtomPromotionsClient.fetch` for Atom
→ direct Atom 200/304 when available
→ only direct Atom 401/403/429 may enter `AtomIndexedPromotionsClient`
→ exact canonical Bing RSS confirmation
→ zero-public-code partner-only compatibility document
→ existing `parse_current_atom_promotions_html`
→ durable source state / freshness
→ existing poster resolver
→ existing embeds/panel/delivery

There is no second Atom monitor and no duplicate movie-artwork pipeline.

## #263 implementation
### Atom recovery
- Direct Atom remains authoritative.
- Only explicit direct Atom HTTP 401/403/429 can invoke indexed fallback.
- Fallback accepts only exact HTTPS `atomtickets.com/promotions`; off-host results and promotion subpages fail closed.
- Search-index snippets never become promo-code proof.
- A confirmed fallback produces a partner-only zero-public-code shell so stale public reusable codes are cleared instead of revived.
- Persisted fallback state stores two independent timestamps:
  - last public-index confirmation
  - last direct Atom access block
- Persisted cooldown lanes survive process restarts:
  - under 30 minutes after index confirmation: no Atom request and no Bing request
  - after 30 minutes but before six hours since direct block: Bing refresh only
  - at/after six hours since direct block: clean direct Atom reprobe
- Bing refresh preserves the original direct-block timestamp so index refreshes cannot postpone direct recovery indefinitely.
- Fallback markers and stale HTTP validators are stripped before direct reprobes.
- A successful direct Atom response immediately returns ordinary ETag/Last-Modified state.
- Status renders `Indexed fallback` and uses the real persisted index/direct-block timestamps instead of one-minute cache-reuse timestamps.

### Movie posters
- Existing Fandango and Gofobo artwork behavior is unchanged.
- Atom retains its strict Atom image-host path.
- SeeItEarly `/movies/...` detail pages can feed the existing poster resolver.
- SeeItEarly `/_next/image` is unwrapped only when the underlying HTTPS asset host is `image.tmdb.org`.
- Wrong SeeItEarly pages, off-domain redirects, insecure schemes, and arbitrary external image hosts fail closed.
- Poster lookup remains best-effort and cannot suppress an otherwise valid ticket/screening result.

## #263 validation evidence
Exact validated head: `52dedfd4813886ce09ee877f19d08d566e233448`.

Linux/Ubuntu Python 3.12 validation on that exact head:
- targeted movie regressions: **94 passed**
- `python -m pip check`: clean
- `python -m compileall -q sniperplug scripts tests`: exit 0
- import smoke: **37 modules, 22 symbols**
- native libsql process suite: **10 passed**
- full pytest suite: **1,328 passed**
- `FULL PYTEST EXIT=0`
- `git diff origin/main...HEAD --check`: clean
- final HEAD remained exact
- only warning: Discord.py/Python `audioop` deprecation, unrelated to this task

GitHub Actions on the exact head did not execute code: jobs had `runner_id=0`, empty runner name, and `steps=[]`. Those were runner-assignment infrastructure failures, not executed code-test failures. Exact-head local Linux validation was used for merge.

Final merge review state:
- PR mergeable
- 7 changed files, scoped to Atom recovery, movie artwork, and regressions/task documentation
- no inline review threads
- no submitted reviews
- no PR comments
- merged with exact-head protection against `52dedfd4813886ce09ee877f19d08d566e233448`

## Production acceptance after #263 deploy
1. Existing July 31 permanent movie panel still upgrades/updates in place and keeps **Check Now**, **Status**, and **Alerts Here** together.
2. **Alerts Here** saves/enables the intended destination for a manager.
3. Atom Status reports either direct healthy state or truthful **Indexed fallback** state with the real index-confirmation and direct-block timestamps.
4. Fresh indexed fallback does not hit Atom or Bing every minute.
5. At the bounded index cadence, Bing can refresh without resetting direct-block age.
6. At/after six hours the runtime reprobes direct Atom and automatically returns to ordinary direct state if access is restored.
7. Indexed fallback never invents, resurrects, or republishes a reusable Atom promo code.
8. Fandango/Gofobo existing artwork remains intact.
9. SeeItEarly movie-detail cards display an allowed TMDB poster when one is present.
10. Missing poster artwork remains a harmless visual fallback and never suppresses a valid screening/ticket result.
11. SeeItEarly remains supplemental discovery and never becomes a direct auto-deliverable issuer.
12. July 31 panel migration, alert destination, exact snowflakes, freshness, dedupe, and feedback remain intact.

Only after these live checks pass call movie tickets restored and close this task.

## Risks / boundaries
- Public search indexes are fallback evidence and can lag the issuer; they are never used to infer current reusable codes.
- If both direct Atom and the exact public index cannot validate, Atom must remain degraded rather than guessing.
- No CAPTCHA evasion, proxy rotation, browser impersonation, account/login scraping, gated-code guessing, or private invitation scraping.
- Poster enrichment is best-effort and must never block ticket discovery/delivery if artwork is unavailable.

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
Deploy latest `main`, then perform the production acceptance checklist above on the existing July 31 movie panel and live source surface. Do not close the movie-ticket task until those production checks pass.