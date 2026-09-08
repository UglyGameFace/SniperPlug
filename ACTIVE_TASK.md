# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end.**

Target outcome: the permanent movie panel remains useful across deployments, current free-ticket/screening sources are freshness-qualified and truthfully classified, managers can configure alerts directly from the panel, and no stale/generic/aggregated result is presented as stronger proof than the source actually provides.

## Scope
`public movie source fetch/discovery → fail-closed parse → per-source state/backoff → durable freshness-qualified cache → actionable permanent panel/latest/status → configured direct-source alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, or site work without an explicit FORCE SWITCH.

## Status
**Implementation and local validation for #262 are complete and #262 is merged. Active phase is live production acceptance of the existing July 31 permanent panel and expanded source coverage.**

#260 merge: `14d15a72277a2c6e6d8f05a27fc6ae12c366efd0`
#261 validated head: `0275ab55f8c2f7f616700aa8c1305430ab610e6f`
#261 merge: `e49acaab9a2c21531407a62ec8cb3622f4d84b6c`
#262 validated head: `ac9bf854b9286152ada113ac1aabd5e306462e71`
#262 merge: `00335c0943c6b9fc214329c8468b35049ac9b23b`

Previous Walmart delivery work (#256/#257) remains merged; its production acceptance is paused by the explicit task switch.

## Production findings before #262
Proven healthy/improved after #260/#261:
- Atom stale month-old cache is hidden while Atom's upstream HTTP 403 is reported truthfully.
- Fandango and Gofobo remain independently healthy when Atom is blocked.
- Gofobo stale CTA cache was replaced and generic CTA-title parsing is covered.

Remaining defects observed live before #262:
- The permanent panel originally posted July 31 still displayed old command-heavy copy because `/movies-panel` never persisted its message/channel ID.
- Setup Status could describe **Use This Channel for Alerts** while Discord did not reliably surface that private component in the visible viewport.
- Auto-packed component layout could visually displace core controls.
- Source coverage was too narrow if only Atom/Fandango/Gofobo were exposed.

## #262 implementation
### Permanent panel migration
- Uses `interaction.message` as the authoritative identity of an old permanent panel and edits that exact historical message in place on the next interaction.
- No channel-history scan, guessed message ID, or delete/repost migration.
- Explicit component rows keep **Check Now · Status · Alerts Here** together.
- **Alerts Here** is durable on the permanent panel, manager-gated, validates bot permissions, saves the exact current channel, enables monitoring, and runs an immediate refresh.
- Alerts Here acknowledges Discord before panel-edit/setup work to avoid interaction timeout races.
- Guide copy is action-first; slash commands remain fallback/advanced controls.
- Registered `/movies status` and `/movies sources` now describe direct, supplemental, and manual source tiers truthfully.

### Source expansion
Direct monitored/auto-deliverable sources remain:
- Atom
- Fandango
- Gofobo

Added automated **supplemental discovery**:
- SeeItEarly public upcoming-screening index
- maximum 3 active leads
- explicit free-pass evidence required
- safe HTTPS host allowlist, bounded size/timeouts, ETag/Last-Modified support
- classified as aggregated discovery, never as the original issuer
- included in Check Now/latest and connected-source health
- never auto-posted to alert channels
- automatic refresh paced to 10 minutes; manual Check Now/scan bypasses pacing
- adjacent-card free-pass context is isolated
- title identity uses the movie-detail slug boundary while preserving visible punctuation/apostrophes/hyphens

Added manual permanent-panel source links:
- AdvanceScreenings
- 1iota

Audited but intentionally not automated without stronger fail-closed contracts:
- Cinefind
- Screening Squad

## #262 validation evidence
Exact validated head: `ac9bf854b9286152ada113ac1aabd5e306462e71`.

Linux/Ubuntu Python 3.12 validation on that exact head:
- targeted movie regressions: **44 passed**
- `python -m pip check`: clean
- `python -m compileall -q sniperplug scripts tests`: exit 0
- import smoke: **37 modules, 22 symbols**
- native libsql process suite: **10 passed**
- full pytest suite: **1,316 passed**
- `FULL PYTEST EXIT=0`
- `git diff origin/main...HEAD --check`: clean
- final HEAD remained exact
- only warning: Discord.py/Python `audioop` deprecation, unrelated to this task

GitHub Actions infrastructure failed before checkout on the exact head and on one explicit rerun for all three workflows (`steps=null`, no logs). Workflow YAML uses normal `ubuntu-latest`; these were not code-test failures. Local exact-head validation is the executable evidence used for merge.

Final merge review state:
- PR mergeable
- 9 changed files, all scoped to movie tickets
- no inline review threads
- no submitted reviews
- no PR comments

## Production acceptance after deploy
1. Interacting with the July 31 permanent panel edits that same message in place to the current action-first panel; no repost required.
2. The panel visibly shows **Check Now**, **Status**, and **Alerts Here** together.
3. **Alerts Here** in `#free-movie-tickets` acknowledges immediately, enables alerts, and saves that exact channel for a manager.
4. Status then shows alerts enabled and the correct destination, direct-source cadence, supplemental cadence, and truthful connected-source health.
5. Current Gofobo results remain real movie/event names, never CTA labels.
6. SeeItEarly appears only as bounded supplemental discovery/health and never claims to be the original pass issuer.
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
Deploy latest `main`, then perform live acceptance on the existing July 31 panel and expanded source surface. Do not close the task until those production checks pass.