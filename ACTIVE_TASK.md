# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: `/movies` shows only recently confirmed official offers/screenings, current source layouts parse safely, healthy sources continue when one source is blocked, Gofobo local screenings persist and deliver, setup/status/latest/panel interactions give truthful health instead of stale results or circular recovery instructions, and delivery/feedback snowflakes remain exact.

## Scope
`official Atom/Fandango/Gofobo fetch → fail-closed parse → per-source state/backoff → durable drop cache → freshness gate → /movies latest/status/panel → configured channel delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**Draft PR #260 is open on branch `fix/movie-tickets-end-to-end-258`. Implementation is complete enough for exact-head validation; do not merge until targeted/full tests are green.**

Previous Walmart delivery work (#256/#257) remains merged on `main`; its production acceptance is paused by the explicit task switch and stays backlog/context only.

## Root cause / findings
- Production Atom requests return HTTP 403 and enter a six-hour access backoff.
- Movie cache rows had no serving-time freshness ceiling, so a source could fail for days/weeks while old rows remained `active=1` and were still presented as current.
- Current Atom promotions can legitimately render `Partner Promotions` without the old mandatory `Film Promotions` section. The old parser treated that as invalid and preserved stale public codes instead of safely accepting zero current public reusable codes.
- Multi-source runtime declared `local_screening` deliverable, but base storage hard-filtered to `public_reusable`; Gofobo screenings could be parsed and then silently discarded.
- Movie config already used TEXT Discord snowflakes because Turso/libSQL can coerce large numeric IDs, while delivery/message/user feedback IDs still used INTEGER.
- The Atom and multi-source startup log lines come from one inherited registered cog, not duplicate pollers.
- Partial source failures were easy to mistake for a complete successful refresh, and setup guidance could imply rerunning setup would fix an upstream access block.

## Implemented in #260
- Added `movie_ticket_recovery.py` with a strict current-Atom compatibility parser, source freshness helpers, and truthful source-health rendering.
- Partner-only Atom promotions pages are accepted only when the existing parser itself finds the exact Partner Promotions heading on an Atom Promotions document; challenge/access-denied pages remain invalid and partner/account-specific codes remain excluded.
- Added a two-hour source confirmation window for user-facing active results. Stale rows remain durable but are hidden from current results.
- Added an internal `include_stale=True` lane only for HTTP 304 recovery; a successful 304 immediately refreshes source success state.
- Production `SnowflakeSafeMovieTicketStore` now persists/lists both `public_reusable` and `local_screening`.
- Added exact-TEXT `movie_ticket_deliveries_v2` and `movie_ticket_feedback_v2` tables for guild/channel/message/user IDs. Legacy sent-drop dedupe is preserved without trusting large legacy snowflakes back into Python.
- Feedback resolution/votes use exact-text IDs for new alerts; unsafe legacy real-world snowflakes are not guessed.
- Registered Atom scan uses the current-layout parser.
- `/movies setup`, `/movies latest`, `/movies scan`, and inherited status source lines now distinguish partial/upstream degradation, hide stale cache, and state that setup cannot bypass official-site 403/429/access blocks.
- Added regressions for current Atom partner-only pages, block pages, stale cache + 304 recovery, Gofobo persistence, 19-digit delivery/feedback snowflakes, source health, and recovery messaging.

## Validation required
- targeted movie parser/store/runtime/guide/feedback tests
- `python -m pip check`
- `python -m compileall -q sniperplug scripts tests`
- `python scripts/smoke_imports.py`
- native libsql process tests where available
- full pytest suite on the exact PR head
- final diff/review inspection

GitHub-hosted Actions is currently infrastructure-blocked before execution on this PR as well (`runner_id=0`, empty runner name, `steps=[]`); those red conclusions are not code-test failures.

## Production acceptance required
After merge/deploy:
1. Old Atom rows are no longer shown as current once Atom confirmation is stale.
2. A valid current Atom page with no public free-ticket codes clears old Atom results safely instead of preserving them as active.
3. Atom 403 remains contained/backed off without poisoning Fandango/Gofobo health.
4. Gofobo local screening leads can persist, appear in `/movies latest`, and deliver when present.
5. `/movies status`, `/movies latest`, setup, scan, and guide-panel source results distinguish healthy/degraded/stale state without fake user-side fixes.
6. Fresh eligible alerts still dedupe and post to configured channels, and new feedback controls round-trip exact Discord IDs.

Only after those live checks pass call movie tickets restored and close this task.

## Risks / boundaries
- No anti-bot bypass, proxy rotation, CAPTCHA evasion, or private/account-source scraping was added.
- Stale rows are hidden, not blindly deleted.
- Only the two already-supported public classifications are deliverable.
- Pre-migration real Discord delivery/feedback snowflakes stored as unsafe legacy INTEGER values are not guessed back into live controls.

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
Validate the exact #260 head locally, fix only evidence-backed failures, then inspect the final diff/review state before merge.