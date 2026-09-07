# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the permanent movie panel remains useful across deployments, current free-ticket/screening sources are freshness-qualified and truthfully classified, managers can configure alerts directly from the panel, and no stale/generic/aggregated result is presented as stronger proof than the source actually provides.

## Scope
`public movie source fetch/discovery → fail-closed parse → per-source state/backoff → durable freshness-qualified cache → actionable permanent panel/latest/status → configured direct-source alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**#260 and #261 are merged. Draft PR #262 is open with implementation frozen pending executable validation. GitHub Actions is currently failing before any workflow step starts, so #262 is not merge-ready yet.**

#260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
#261 validated head: `0275ab55f8c2f7f616700aa8c1305430ab610e6f`
#261 merge: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`
#261 full-suite evidence: **1,301 passed**, native libsql **10 passed**, compile/import/pip checks green.

#262 code candidate before this bookkeeping commit: `b10e727e1c92583d7e106ebf2f824f6ff9bba83d`
#262 PR: `https://github.com/UglyGameFace/SniperPlug/pull/262`

GitHub-hosted workflow attempts on `b10e727e1c92583d7e106ebf2f824f6ff9bba83d`:
- Import smoke check run `34170139111`: failure before execution; job has no logs and no steps.
- Python Check run `34170139110`: failure before execution; job has no logs and no steps.
- Python tests run `34170139157`: failure before execution; job has no logs and no steps.

These are infrastructure/runner-start failures, not code-test failures. No green test claim is made for #262 yet.

Previous Walmart delivery work (#256/#257) remains merged; its production acceptance is paused by the explicit task switch.

## Production findings after #261
Proven healthy/improved:
- Atom stale month-old cache is hidden while Atom's upstream HTTP 403 is reported truthfully.
- Fandango and Gofobo remain independently healthy when Atom is blocked.
- Gofobo stale CTA cache was replaced and generic CTA-title parsing is covered by #261.

Remaining UX/coverage defects shown live:
- The permanent panel originally posted July 31 still displays its old command-heavy embed because `/movies-panel` never persisted the panel message/channel ID. Persistent custom IDs keep its controls alive, but Discord does not rewrite the historical message body after code updates.
- Setup Status can describe **Use This Channel for Alerts** while Discord may not visibly render that private component in the user's viewport. A durable setup action must live on the permanent panel itself.
- The old panel lets Discord.py auto-pack controls, so core actions can be visually displaced by source-link buttons.
- The source surface is too narrow if only Atom/Fandango/Gofobo are exposed.

## #262 root cause / implementation
### Permanent panel migration
- Use `interaction.message` as the authoritative identity of an old permanent panel. On the next dropdown/button interaction, edit that exact historical message in place with the current embed and view.
- No channel-history scan, guessed message ID, or delete/repost migration.
- Use explicit component rows:
  - row 0: task dropdown
  - row 1: **Check Now · Status · Alerts Here**
  - row 2: Atom · Fandango · Gofobo
  - row 3: supplemental/manual screening directories
- Add durable **Alerts Here** directly to the permanent panel. It shares the same Manage Server, bot-permission, exact-channel config, and immediate-refresh path as the private backup action.
- **Alerts Here acknowledges the Discord interaction before editing the historical panel**, preventing message-edit latency from causing a false interaction timeout.
- Rewrite the permanent guide to be action-first. Slash commands remain advanced fallbacks rather than the main instructions.
- Registered `/movies status` and `/movies sources` are overridden so fallback commands describe the expanded source model truthfully instead of inheriting old three-source/official-only copy.

### Source expansion
Direct monitored/auto-deliverable sources remain:
- Atom
- Fandango
- Gofobo

Added automated **supplemental discovery**:
- SeeItEarly public upcoming-screening index
- maximum 3 active leads
- requires explicit `N free pass(es)` evidence paired with a same-card title
- safe HTTPS host allowlist, bounded response size/timeouts, ETag/Last-Modified support
- classified/labeled as aggregated discovery, never as the original issuer
- included in Check Now/latest and connected-source health
- **never auto-posted to alert channels**, preventing duplicate aggregator/direct-source blasts
- automatic SeeItEarly refresh is paced to **10 minutes** while manual Check Now/scan bypasses that pacing
- free-pass context is cleared after each movie card so one title cannot donate its free-pass count to the next title
- current combined-card title extraction uses the safe movie-detail slug as an identity boundary while preserving visible punctuation/apostrophes/hyphens such as `Oasis: Don't Look Back in Anger` and `Spider-Man: Brand New Day`

Added manual screening-directory links on the permanent panel:
- AdvanceScreenings
- 1iota

Additional public candidates audited but not automated without stronger fail-closed source contracts:
- Cinefind
- Screening Squad

## Validation evidence / required
Implemented regression coverage now includes:
- old permanent panel self-refreshes the exact interaction message
- fixed component rows keep Check Now/Status/Alerts Here together
- Alerts Here acknowledgement occurs before old-panel migration/setup work
- SeeItEarly production-shaped combined-card title parsing
- punctuation/apostrophe/hyphen preservation at the slug identity boundary
- adjacent-card free-pass count cannot bleed into another title
- off-site SeeItEarly URLs fail closed
- SeeItEarly active discoveries capped at 3
- supplemental discovery embed cannot masquerade as direct issuer proof
- registered runtime scans SeeItEarly but its scanner never calls direct alert delivery
- automatic SeeItEarly refresh is paced while manual refresh bypasses pacing
- registered status/sources expose direct vs supplemental vs manual source truth
- four-source backoff/outage tests updated
- older #258/#261 wording/actionability regressions updated only where the expanded source model changed the contract

Still required before merge-ready:
- execute targeted movie regression set on exact final head
- `python -m pip check`
- `python -m compileall -q sniperplug scripts tests`
- import smoke
- native libsql process suite
- full pytest suite on exact final head
- confirm final Actions/review state

Current review state before this bookkeeping update:
- inline review threads: none
- submitted reviews: none
- PR comments: none
- changed files: 9 expected movie-task files only

## Production acceptance after merge/deploy
1. Interacting with the July 31 permanent panel edits that same message in place to the current action-first panel; no repost is required.
2. The permanent panel visibly shows **Check Now**, **Status**, and **Alerts Here** together.
3. **Alerts Here** in `#free-movie-tickets` acknowledges immediately, enables alerts, and saves that exact channel for a manager.
4. Status then shows alerts enabled and the correct destination, direct-source cadence, supplemental cadence, and truthful source health.
5. Current Gofobo results remain real movie/event names, never CTA labels.
6. SeeItEarly appears as bounded supplemental discovery/health only and never claims to be the original pass issuer.
7. SeeItEarly does not create automatic duplicate channel alerts and is not fetched every 60 seconds automatically.
8. AdvanceScreenings and 1iota are available as manual source links.
9. Atom stale-cache handling, direct-source isolation, exact snowflakes, dedupe, and feedback remain intact.

Only after these live checks pass call movie tickets restored and close this task.

## Risks / boundaries
- No anti-bot bypass, proxy rotation, CAPTCHA evasion, login/account scraping, or gated-code republishing.
- Aggregators are labeled as aggregators and cannot silently become direct-source proof.
- No theater/date/time is guessed when a source requires ZIP/account lookup.
- SeeItEarly automation is bounded and supplemental; direct automatic alerts remain Atom/Fandango/Gofobo only.
- Stale rows remain durable but are hidden from current results.

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
Treat the resulting bookkeeping commit as the final #262 head, retry/inspect GitHub Actions once, and run the full exact-head validation as soon as an executable runner is available. Keep PR #262 draft until that evidence exists; after merge, perform live July-31-panel and expanded-source acceptance before closing this task.
