# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the existing July 31 permanent movie panel stays useful across deployments; current free-ticket and screening sources are freshness-qualified and truthfully classified; managers can configure alerts directly from the panel; Atom degradation does not poison the whole movie surface; and movie cards include trustworthy poster artwork when the connected source exposes it.

## Scope
`public movie source fetch/discovery → fail-closed parse → per-source state/backoff/fallback → durable freshness-qualified cache → poster enrichment → actionable permanent panel/latest/status → configured direct-source alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**#262 is merged and its production acceptance exposed two remaining defects now owned by draft PR #263: Atom direct HTTP 403 and missing dependable artwork on supplemental movie cards. #263 implementation is frozen pending executable exact-head validation.**

Prior movie milestones:
- #260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
- #261 validated head: `0275ab55f8c2f7f616700aa8c1305430ab610e6f`
- #261 merge: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`
- #262 validated head: `ac9bf854b9286152ada113ac1aabd5e306462e71`
- #262 merge: `00335c0943c6b9fc214329c8468b35049ac9b23b`

Previous Walmart delivery work (#256/#257) remains merged; its production acceptance is paused by the explicit movie-task switch.

## Production findings after #262
Proven healthy:
- The July 31 permanent panel can migrate in place and the action-first controls work.
- Fandango, Gofobo, and SeeItEarly can continue independently when Atom fails.
- SeeItEarly supplemental discovery is bounded and does not auto-post as a direct issuer.
- Exact Snowflake-safe movie persistence, source freshness, dedupe, and feedback remain in the registered runtime.

Remaining defects observed live:
1. Atom's canonical promotions page is publicly live, but the hosted SniperPlug aiohttp request receives HTTP 403 and the source becomes stale/unavailable.
2. Fandango/Gofobo already expose artwork paths, but SeeItEarly cards had no poster resolution path, leaving live movie cards visually incomplete.

## Root cause / research for #263
### Atom
- Atom's canonical `/promotions` page is the issuer surface for current promo terms.
- Current public evidence shows the canonical page is indexed and reachable outside the hosted SniperPlug request.
- Atom's official Partner API is automation-friendly for productions/showtimes/order metadata but does not provide a documented promotions-list endpoint, so an API-key-only migration would not solve this source.
- Bypassing Atom's access controls with CAPTCHA evasion, rotating proxies, browser impersonation, or login scraping is outside scope.
- A bounded public search-index confirmation can verify only the exact canonical Atom promotions URL. It is weaker evidence than a direct issuer fetch and therefore cannot supply or infer a reusable promo code.

### Artwork
- The existing runtime already has one poster resolver shared by latest/results/delivery embeds.
- Fandango and Gofobo already provide source-specific image markers.
- Atom artwork already accepts only known Atom image hosts.
- SeeItEarly movie-detail pages expose poster imagery through their own Next image proxy backed by `image.tmdb.org`; this can be safely unwrapped without requiring a separate TMDB API key.

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

## #263 changes
### Atom access recovery
- Direct Atom remains authoritative.
- Only explicit direct Atom HTTP 401/403/429 can invoke indexed fallback.
- Fallback accepts only HTTPS `atomtickets.com/promotions` exactly; off-host results and promotion subpages fail closed.
- Search-index snippets never become promo-code proof.
- A confirmed fallback produces a partner-only zero-public-code shell so stale public reusable codes are cleared instead of revived.
- Fallback state persists two timestamps in the existing source-state marker:
  - last public-index confirmation
  - last direct Atom access block
- Cooldown lanes are persisted across restarts:
  - under 30 minutes after index confirmation: no Atom request and no Bing request
  - after 30 minutes but before six hours: refresh Bing only
  - after six hours: clean direct Atom reprobe with fallback validators stripped
- A Bing refresh preserves the original direct-block timestamp, so refreshing the index cannot postpone direct Atom recovery forever.
- A successful direct Atom reprobe immediately returns ordinary ETag/Last-Modified state.
- Status renders `Indexed fallback` and uses the real persisted index-confirmation timestamp rather than the minute-by-minute scan-loop cache-reuse time.

### Movie posters
- Existing Fandango and Gofobo artwork behavior is unchanged.
- Atom keeps its strict Atom-host artwork path.
- SeeItEarly `/movies/...` detail pages can now feed the existing poster resolver.
- SeeItEarly's `/_next/image` proxy is unwrapped only when the underlying HTTPS host is `image.tmdb.org`.
- Wrong SeeItEarly pages, off-domain redirects, insecure schemes, and arbitrary external image hosts fail closed.
- Existing Discord embed image rendering is reused; no second render path was introduced.

## Regression coverage added
`tests/test_atom_indexed_fallback_263.py` covers:
- exact canonical Atom RSS acceptance and off-domain/subpage rejection
- backward-compatible fallback marker parsing
- indexed shell cannot invent a reusable code
- initial direct 403 → indexed confirmation
- fresh index state performs zero network requests
- 30-minute index refresh does not reprobe Atom or reset the six-hour direct block timer
- six-hour direct reprobe strips fallback validators and restores ordinary direct state
- non-access HTTP failures do not invoke fallback
- source health displays the real index/direct-block observations, not cache-reuse time

`tests/test_movie_ticket_images_copy.py` adds:
- SeeItEarly movie-detail poster extraction through its image proxy
- TMDB image allowlist enforcement
- wrong-page/off-domain image rejection

## Validation / results
### Existing #262 evidence
Exact #262 validated head `ac9bf854b9286152ada113ac1aabd5e306462e71` on Linux/Ubuntu Python 3.12:
- targeted movie regressions: 44 passed
- pip check clean
- compileall exit 0
- import smoke: 37 modules, 22 symbols
- native libsql process suite: 10 passed
- full pytest: 1,316 passed
- diff check clean

### #263 current validation state
- Structural diff review performed against current `main`; six implementation/test files are scoped to Atom recovery and movie artwork before this task-record update.
- GitHub Actions triggered on implementation head `69fe8b196597174390e7e70dc4072aa388dba5fc` but all three jobs failed before checkout: `runner_id=0`, empty runner name, and `steps=[]` for Python tests, Python Check, and Import smoke check. These are runner/infrastructure failures, not executed code-test failures.
- Exact-head executable #263 validation is still required. Do not mark ready or merge until targeted tests, pip check, compileall, import smoke, native libsql, full pytest, and diff check pass on the final branch head.

## Cleanup / conflict review
- No anti-bot bypass or duplicate Atom fetcher was introduced.
- Temporary import-time fallback patching used during investigation was removed; the authoritative `AtomPromotionsClient.fetch` owns the fallback behavior deterministically.
- Existing Fandango/Gofobo source and artwork paths remain authoritative for those providers.
- SeeItEarly remains supplemental and non-auto-deliverable.
- No unrelated backlog work is included in #263.

## Risks / boundaries
- Public search indexes are fallback evidence and can lag the issuer; they are never used to infer current reusable codes.
- If both direct Atom and the exact public index cannot validate, Atom must remain degraded rather than guessing.
- Current public Atom promotions may contain partner-supplied/private codes; those are not republished or fabricated.
- No CAPTCHA evasion, proxy rotation, browser impersonation, account/login scraping, gated-code guessing, or private invitation scraping.
- Poster enrichment is best-effort and must never block ticket discovery/delivery if artwork is unavailable.

## Production acceptance after #263 deploy
1. Atom no longer sits indefinitely as a month-old stale source solely because the hosted request receives 403; Status truthfully reports indexed fallback when that is the active evidence tier.
2. Indexed fallback does not resurrect any old reusable Atom code and never presents partner/private codes as public.
3. Repeated one-minute movie scans do not hit Atom or Bing every minute while fallback state is fresh.
4. Bing is refreshed on the bounded index cadence and direct Atom is reprobed on the longer access-block cadence.
5. If Atom direct access recovers, Status returns to ordinary direct-source health automatically.
6. SeeItEarly cards display a real movie poster when its movie-detail page exposes an allowed TMDB asset.
7. Fandango/Gofobo/Atom existing artwork remains intact.
8. Missing artwork remains a harmless visual fallback, never a reason to suppress a valid ticket/screening result.
9. July 31 panel migration, alert destination, exact snowflakes, freshness, dedupe, and feedback remain intact.

Only after executable validation and these live checks pass call movie tickets restored and close this task.

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
Run exact-head #263 validation in the known-good Python 3.12/native-libsql Termux environment. If every executable check is green, update PR #263's external validation record without changing the branch head, mark it ready, merge with exact-head protection, deploy, then perform the production acceptance checklist above.
