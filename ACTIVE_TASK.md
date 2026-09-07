# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: `/movies` shows only recently confirmed official offers/screenings, current source layouts parse safely, healthy sources continue when one source is blocked, Gofobo local screenings persist and deliver as actionable movie/event leads, setup/status/latest/panel interactions work without command memorization, and delivery/feedback snowflakes remain exact.

## Scope
`official Atom/Fandango/Gofobo fetch → fail-closed parse → per-source state/backoff → durable drop cache → freshness gate → actionable /movies latest/status/panel → configured channel delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**PR #260 and corrective follow-up PR #261 are merged. Production acceptance is now the only remaining gate before this task can be called restored.**

#260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
#261 validated head: `0275ab55f8c2f7f616700aa8c1305430ab610e6f`
#261 merge commit: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`

#261 exact-head validation evidence:
- targeted movie regression set: **57 passed**
- `python -m pip check`: passed
- compileall: exit 0
- import smoke: **37 modules / 22 symbols**, exit 0
- native libsql process suite: **10 passed**, exit 0
- full pytest suite: **1,301 passed**, exit 0
- only 1 upstream `audioop` deprecation warning
- final diff: only 5 expected files
- inline review threads: none
- submitted reviews: none

GitHub-hosted Actions still reports infrastructure failures before execution on the validated head; sampled jobs have no executed steps. Local Linux Python 3.12/native-libsql validation is the code evidence for #261.

Previous Walmart delivery work (#256/#257) remains merged on `main`; its production acceptance is paused by the explicit task switch and stays backlog/context only.

## Root cause / findings
- Production Atom requests return HTTP 403 and enter source backoff.
- Before #260, stale movie cache rows could remain active indefinitely and be presented as current.
- Current Atom promotions can validly render `Partner Promotions` without the old `Film Promotions` section.
- Before #260, Gofobo `local_screening` results could be parsed and then silently discarded by storage filtering.
- Delivery/feedback Discord snowflakes required exact TEXT storage under Turso/libSQL.
- After #260, production proved Gofobo persistence worked but exposed a second defect: live Gofobo alerts could use CTA text such as **`LEARN MORE`** as the movie title.
- Current Gofobo cards can separate movie/event headings from generic CTA links and can use deeper heading levels.
- A persisted bad Gofobo row could survive a parser deploy through HTTP 304 without a fresh response body.
- Setup Status still required users to reconstruct slash-command syntax instead of offering a direct action.

## Implemented
#260:
- source freshness gate with durable-but-hidden stale cache
- current Atom compatibility parser
- independent source degradation/backoff
- Gofobo `local_screening` persistence/delivery
- exact-TEXT delivery/feedback snowflakes
- truthful setup/latest/status recovery messaging

#261:
- rejects generic Gofobo CTA labels as titles and strips CTA suffixes from mixed card text
- captures h1-h6 headings in the official upcoming section
- pairs real title headings with following official CTA detail links
- coalesces duplicate title candidates and prefers title-specific official URLs
- falls back to the official Gofobo ZIP search when a specific URL cannot be proven
- recognizes the current official Gofobo image CDN host
- shortens Gofobo restriction copy and explicitly keeps theater/date/time ZIP-gated
- forces one unconditional Gofobo body fetch per process before normal ETag/Last-Modified behavior resumes, replacing old bad CTA cache after deploy
- adds **Use This Channel for Alerts** to private Setup Status with Manage Server and channel-permission checks
- makes guide actions button-first while keeping slash commands available

## Remaining production acceptance
After deploy/restart:
1. No Gofobo alert uses `LEARN MORE`, `MORE INFO`, `VIEW DETAILS`, or similar CTA text as a movie/event title.
2. Current Gofobo movie/event names appear when exposed by the official page.
3. Title-specific official links are used when proven; otherwise the alert uses official ZIP search without inventing theater/date/time.
4. Gofobo alerts are concise enough to answer what the movie/event is, whether a public code exists, and what to do next.
5. The first post-deploy Gofobo scan replaces the existing bad CTA cache instead of re-confirming it through HTTP 304.
6. Setup Status exposes one-click current-channel configuration for a manager and persists/delivers to that exact channel.
7. Atom stale-cache handling, source isolation, exact snowflakes, dedupe, and feedback remain intact.

Only after these live checks pass call movie tickets restored and close this task.

## Risks / boundaries
- No anti-bot bypass, proxy rotation, CAPTCHA evasion, or private/account-source scraping is added.
- Stale rows are hidden, not blindly deleted.
- Only the already-supported public classifications are deliverable.
- Gofobo theater/date/time are not guessed when the public source requires ZIP/account lookup.
- The unconditional Gofobo body fetch happens once per process; normal conditional request behavior resumes afterward.
- Unsafe pre-migration legacy INTEGER snowflakes are not guessed back into live controls.

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
Deploy/restart `main` at or after merge commit `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`, then run live `/movies latest` and Setup Status acceptance against the real Gofobo result before closing this task.