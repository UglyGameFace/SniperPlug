# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the existing July 31 permanent movie panel stays useful across deployments; current free-ticket and screening sources are freshness-qualified and truthfully classified; managers can configure alerts directly from the panel; Atom degradation does not poison the whole movie surface; movie cards include trustworthy poster artwork when available; manual results have obvious open/claim actions; and users can optionally save a private ZIP/radius so local browsing does not treat long road trips as nearby screenings.

## Scope
`public movie source fetch/discovery → fail-closed parse → per-source state/backoff/fallback → durable freshness-qualified cache → poster enrichment → actionable panel/latest/status → obvious result actions → optional per-user ZIP/radius local view → configured direct-source alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**#264 is merged. Draft #265 on `fix/movie-user-location-265` now contains the optional per-user ZIP/radius implementation plus interaction/latency hardening. Current candidate head is `0c68592b5b4d160832832935a9c676554653ba12`; exact-head executable validation is still blocked by GitHub Actions failing before checkout.**

Movie milestones:
- #260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
- #261 merge: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`
- #262 merge: `00335c0943c6b9fc214329c8468b35049ac9b23b`
- #263 merge: `85e26ffa76a8a76fe6aac32c98393e2cbd5c54ee`
- #264 validated head: `01f638eb5e0c80564fddc00d2eb8a05a81206042`
- #264 merge: `6e745529a13d881a9e651d2cc6dfd221cb7ef4e1`
- #265 interaction hardening: `c5635fe1cda8ecad0acfc0f4c04fbb804b5d385a`
- #265 interaction regressions: `63d3aa5b600b7b5957330b509bc85c1eb82151db`
- #265 bounded lookup latency: `58221e148db26d081af1a83642db150b47b8f1ee`
- #265 latency regressions / candidate head: `0c68592b5b4d160832832935a9c676554653ba12`

Previous Walmart delivery work remains merged; its production acceptance is paused by the explicit movie-task switch.

## Production finding after #264 work
SeeItEarly correctly exposed screening discovery, but a user entering ZIP `06484` on the source site still saw New York screening options around 60.9 miles away. A free screening that requires a roughly 120-mile round trip is not meaningfully local for many users.

The correct product behavior is optional personalization rather than a forced setup wall:
- users may browse all results without a ZIP;
- a user may privately save a ZIP and choose 10/25/50/100 miles;
- Local mode must fail closed on distance/location proof;
- Show All must remain available at any time;
- one user's movie ZIP must never alter guild-wide automatic alert configuration.

## Authoritative execution paths
### Manual results
`/movies latest` and July-panel **Check Now**
→ freshness-qualified `store.list_active_drops(limit=8)`
→ poster-aware embeds
→ shared `build_movie_result_actions_view`
→ numbered open/claim links + optional personal location controls

### Personal location controls
`Set / Change ZIP`
→ immediately open Discord modal before any Turso read
→ strict US ZIP/radius validation
→ live Zippopotam.us ZIP/nearby resolution
→ cache the resolved location for the immediate Local view
→ exact-TEXT per-user persistence
→ immediate conservative Local result view

`Use Local`
→ acknowledge/defer Discord interaction before DB/network work
→ saved ZIP/radius
→ nearest postal places from Zippopotam.us
→ Haversine distance bound
→ up to 8 SeeItEarly public city pages inside radius, at most 4 concurrent
→ accept only explicitly **Free** movie links inside the city's **What's playing** section
→ hide distance-unverified local-screening rows

`Show All`
→ freshness-qualified broad feed without deleting the saved ZIP

`Clear ZIP`
→ defer first
→ delete only that user's movie-location preference
→ guild alert destination/config remains untouched

### Public automatic alerts
Existing direct-source delivery and `MovieTicketFeedbackView` remain unchanged. Per-user location is manual/ephemeral browsing context only.

## #265 implementation
- Added `sniperplug/services/movie_user_location.py`.
- Added exact-TEXT `movie_ticket_user_location_v2` keyed by Discord user ID.
- ZIP accepts a valid five-digit US ZIP or ZIP+4 and stores the first five digits.
- Radius is restricted to 10, 25, 50, or 100 miles; default is 25.
- Zippopotam.us is used as the no-key postal resolver for city/state/coordinates and nearest postal places.
- Nearby postal places are distance-bounded with Haversine math before SeeItEarly pages are checked.
- ZIP resolution is cached 30 minutes; SeeItEarly city pages are cached 10 minutes.
- ZIP validation and the immediate Local view now share the same location cache instead of repeating the postal lookup.
- At most 8 nearby city pages are checked per Local lookup and at most 4 are fetched concurrently, preventing a source timeout from serially stretching one click past a minute.
- Local mode preserves non-screening/global promotions.
- SeeItEarly screening leads require a matching movie on an in-radius public city page.
- City parsing is restricted to the **What's playing** section and requires an explicit **Free** label. Paid entries, unknown-status entries, Releasing Soon links, and off-site links cannot prove a local free screening.
- Gofobo/other title-only local-screening rows remain behind Show All until their feed exposes distance-safe locality proof.
- The shared #264 result view exposes `Set / Change ZIP`, `Use Local`, `Show All`, and `Clear ZIP` on the fifth Discord component row while preserving up to 8 numbered result links on rows 0-3.
- Set/Change ZIP opens its modal before any database call so a slow Turso worker cannot cause Discord's generic interaction timeout.
- Use Local acknowledges Discord before database/network work.
- DB load/save/clear failures, locality refresh failures, and card-render failures return visible ephemeral errors instead of killing the interaction.
- Locality refresh failure remains fail-closed: unverified screening rows are hidden while non-screening/global results remain usable.
- Location controls live only on ephemeral manual result views; they do not modify guild movie alert configuration.

## Regression coverage
`tests/test_movie_user_location_265.py` covers:
- ZIP / ZIP+4 normalization and invalid ZIP rejection
- allowed radius values
- exact large Discord user-ID TEXT round trip
- save/read/clear lifecycle
- national promo preservation in Local mode
- in-radius SeeItEarly match retention
- roughly 60-mile New York example excluded from a 25-mile Shelton preference
- distance-unverified Gofobo/local rows hidden
- fail-closed behavior when city pages cannot be confirmed
- Haversine distance sanity
- optional location controls and row placement
- no guild movie config mutation
- Set ZIP opens the modal without a pre-modal DB read
- Use Local defers before slow result work
- unexpected local-proof refresh failure keeps global results and hides unverified screenings

`tests/test_movie_user_location_latency_265.py` covers:
- live ZIP validation populates the cache reused by the immediate Local lookup
- city-page checks run concurrently but never above the configured concurrency bound

`tests/test_movie_user_location_parser_265.py` covers:
- only explicitly Free entries in What's playing are accepted
- Paid and unknown-status entries are rejected
- Releasing Soon links are rejected
- a Free badge inside the movie link is accepted
- off-site links are rejected

Existing #264 result-action regressions were updated to distinguish source link buttons from the new personal location controls and to require movie titles in action labels.

## Existing behavior that must remain intact
- #264 visible numbered result buttons in `/movies latest` and panel Check Now.
- Direct Atom remains authoritative; only access-block statuses can use the indexed fallback.
- Indexed Atom fallback never infers reusable promo codes.
- Atom fallback cooldown lanes remain intact.
- Fandango/Gofobo artwork remains intact.
- SeeItEarly poster enrichment remains allowlisted and supplemental/non-auto-deliverable.
- July 31 panel migration, guild alert destination, exact Snowflakes, source freshness, delivery dedupe, and feedback remain intact.

## Validation / results
### #264 baseline
Exact head `01f638eb5e0c80564fddc00d2eb8a05a81206042` on Linux/Ubuntu Python 3.12:
- targeted movie regressions: 61 passed
- pip check clean
- compileall exit 0
- import smoke: 37 modules / 22 symbols
- native libsql: 10 passed
- full pytest: 1,336 passed
- diff check clean

### #265 structural validation completed
- final scoped file list inspected; changes remain inside the active movie task
- PR is mergeable with no base conflicts
- PR review/comment timeline currently has no review threads/comments
- Zippopotam.us current documentation confirms `/{country}/{postalcode}` and `/nearby/{country}/{postalcode}`; nearby is limited to 11 places
- current SeeItEarly city pages confirm `/cities/<city>-<state>` and a `What's playing` section with explicit Free/Paid classifications
- interaction timing paths inspected after hardening
- latency path inspected after cache reuse + concurrency cap

### #265 executable validation blocker
Current candidate head before this task-record commit: `0c68592b5b4d160832832935a9c676554653ba12`.

GitHub-hosted workflows for #265 repeatedly terminate before executing repository steps. The latest exact-head attempts return jobs with `steps=null` / no logs. A manual re-run was accepted by GitHub but produced a fresh job ID with the same no-steps failure. These are infrastructure failures, not executed test failures.

This ChatGPT runtime has no mounted SniperPlug checkout and cannot resolve github.com directly, so it cannot truthfully substitute a local full-suite run.

Still required before ready/merge:
- targeted #265 + affected #264/movie tests
- `python -m pip check`
- `python -m compileall -q sniperplug scripts tests`
- `python scripts/smoke_imports.py`
- native libsql process tests
- full `PYTHONHASHSEED=0 python -m pytest -q`
- `git diff origin/main...HEAD --check`
- exact final HEAD verification after this task-record commit
- final PR diff / review-thread inspection

Do not mark #265 ready or merge it until those executable checks run on the exact final head.

## Production acceptance after #265 deploy
1. Manual result views visibly show Set/Change ZIP, Use Local, Show All, and Clear ZIP.
2. Set/Change ZIP opens promptly even if Turso is slow.
3. Use Local acknowledges promptly before DB/source work.
4. A user can browse without ever setting ZIP.
5. Saving ZIP `06484` and radius 25 validates as Shelton, CT and immediately returns a conservative Local view without repeating the ZIP lookup.
6. A New York option around 60 miles away is not presented as local under the 25-mile preference.
7. Only explicitly free SeeItEarly city entries may satisfy Local mode.
8. Show All restores broader screening discovery without erasing the saved preference.
9. Clear ZIP removes only that user's preference.
10. Local-source failure hides unverified screenings rather than breaking the interaction.
11. Another user's results and the guild's automatic alert destination are unchanged.
12. #263/#264 Atom fallback, posters, source health, numbered result buttons, public feedback controls, freshness, and dedupe remain intact.

Only after exact-head executable validation and these live checks pass call the movie-ticket task restored.

## Risks / boundaries
- Zippopotam.us nearby lookup returns only the nearest 11 postal places. Local mode is intentionally conservative and must not claim exhaustive coverage for a wide radius.
- At most 8 nearby city pages are checked per Local lookup, with at most 4 concurrent source requests.
- Failure to confirm locality hides a screening from Local mode rather than falsely locating it; Show All remains the escape hatch.
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
Treat the post-task-record #265 head as frozen. Run exact-head Python 3.12/native-libsql targeted and full validation as soon as a runner can actually execute steps. Only then mark the PR ready, merge that exact tested SHA, deploy, and complete the live production acceptance list.
