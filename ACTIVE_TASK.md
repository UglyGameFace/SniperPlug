# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: `/movies` shows only recently confirmed official offers/screenings, current source layouts parse safely, healthy sources continue when one source is blocked, Gofobo local screenings persist and deliver as actionable movie/event leads, setup/status/latest/panel interactions work without command memorization, and delivery/feedback snowflakes remain exact.

## Scope
`official Atom/Fandango/Gofobo fetch → fail-closed parse → per-source state/backoff → durable drop cache → freshness gate → actionable /movies latest/status/panel → configured channel delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**PR #260 is merged as `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`, but production acceptance exposed an additional Gofobo/actionability defect. Draft follow-up PR #261 is open on branch `fix/movie-ticket-actionability-261`.**

Validated #260 head: `dab76e36193584ed42245ef2c3e21e744bd91ecc`.

#260 validation evidence:
- targeted movie regression set: **67 passed**
- `python -m pip check`: passed
- compileall: passed, exit 0
- import smoke: **37 modules / 22 symbols**, exit 0
- native libsql process tests: **10 passed**, exit 0
- full suite: **1,295 passed**, exit 0

Previous Walmart delivery work (#256/#257) remains merged on `main`; its production acceptance is paused by the explicit task switch and stays backlog/context only.

## #260 root cause / fixes
- Production Atom requests return HTTP 403 and enter a six-hour access backoff.
- Movie cache rows had no serving-time freshness ceiling, so a source could fail for days/weeks while old rows remained `active=1` and were still presented as current.
- Current Atom promotions can legitimately render `Partner Promotions` without the old mandatory `Film Promotions` section. The old parser treated that as invalid and preserved stale public codes instead of safely accepting zero current public reusable codes.
- Multi-source runtime declared `local_screening` deliverable, but base storage hard-filtered to `public_reusable`; Gofobo screenings could be parsed and then silently discarded.
- Movie config already used TEXT Discord snowflakes because Turso/libSQL can coerce large numeric IDs, while delivery/message/user feedback IDs still used INTEGER.
- #260 added freshness-bounded results, current Atom compatibility, Gofobo `local_screening` persistence, exact-TEXT delivery/feedback snowflakes, and truthful source degradation/status messaging.

## Production acceptance after #260
Proven improved:
- Atom now reports stale/unavailable after its month-old confirmation and old cached Atom results are hidden.
- Atom HTTP 403 no longer poisons healthy Fandango/Gofobo source health.
- Fandango and Gofobo both confirmed healthy in the same live status result.
- Gofobo `local_screening` rows now persist and appear in `/movies latest`.

Acceptance failure discovered:
- The live Gofobo result used **`LEARN MORE` as the movie title**.
- Gofobo's current public homepage lists real upcoming titles, while the parser accepted the card CTA as a screening candidate.
- The resulting alert was generic and oversized: it did not identify the actual movie/event or give a concise useful next step.
- The Setup Status panel still told managers to reconstruct `/movies setup alert_channel:#...` manually instead of providing a direct action.

## #261 root cause / implementation
- Gofobo card titles and generic CTA links can be separate DOM elements; the parser treated arbitrary links inside the upcoming section as titles.
- The generic-title denylist omitted CTA labels such as `LEARN MORE` and `MORE INFO`.
- Heading capture stopped at h3 even though current cards may use deeper heading levels.
- Title-only candidates had no explicit safe fallback when a title-specific link could not be associated.

Implemented in #261:
- Reject generic CTA labels as movie/event titles and strip CTA suffixes from mixed card text.
- Capture h1-h6 headings within the official upcoming section.
- Pair a real heading title with a following generic CTA link when that CTA carries the title-specific official URL.
- Coalesce duplicate title candidates and prefer title-specific official Gofobo detail URLs.
- Fall back to the official Gofobo ZIP search when a title is known but a title-specific URL cannot be proven.
- Accept the current official Gofobo image CDN host observed on the public site.
- Make Gofobo validity/restriction text state explicitly that theater/date/time are ZIP-gated and must be checked on Gofobo.
- Add **Use This Channel for Alerts** to the private Setup Status result. The action requires Manage Server, validates bot permissions, saves the exact channel snowflake, and immediately runs a source scan.
- Rework the guide so primary actions are buttons first and slash-command syntax is secondary.

## Validation required for #261
- targeted Gofobo parser/actionability tests
- movie guide/status action tests
- existing movie parser/store/runtime/feedback regressions
- `python -m pip check`
- `python -m compileall -q sniperplug scripts tests`
- `python scripts/smoke_imports.py`
- native libsql process tests where available
- full pytest suite on the exact PR head
- final diff/review inspection

GitHub-hosted Actions has recently been infrastructure-blocked before execution (`runner_id=0`, empty runner name, `steps=[]`); red conclusions with no runner/steps are not code-test failures.

## Remaining production acceptance
After #261 merge/deploy:
1. No Gofobo alert may use CTA text such as `LEARN MORE`, `MORE INFO`, or `VIEW DETAILS` as a movie/event title.
2. Current Gofobo upcoming movie/event names must appear in the alert when the official page exposes them.
3. Title-specific official links are used when proven; otherwise the alert falls back to Gofobo's official ZIP search without inventing theater/date/time.
4. Gofobo alerts are concise enough to answer: what movie/event is this, is a public code exposed, and what do I do next?
5. Setup Status exposes one-click current-channel configuration for a manager and successfully persists/delivers to that exact channel.
6. Atom stale-cache handling, source isolation, exact snowflakes, dedupe, and feedback behavior remain intact.

Only after these live checks pass call movie tickets restored and close this task.

## Risks / boundaries
- No anti-bot bypass, proxy rotation, CAPTCHA evasion, or private/account-source scraping is added.
- Stale rows are hidden, not blindly deleted.
- Only the two already-supported public classifications are deliverable.
- Gofobo theater/date/time are not guessed when the public source requires a ZIP/account lookup.
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
Validate the exact #261 head locally, fix only evidence-backed failures, then inspect final diff/review state before merge and repeat production acceptance with a real Gofobo result plus one-click setup.