# Active Task

## November 2 follow-up: high-value Walmart public alert quality (PR #305, open)

- Owner's live Discord screenshots show $9.92 jewelry, $10 earrings, $9.97 Switch game code, $5.99 EA Play subscription, and $10 Razer Gold digital credit posted as "Extreme exact-price finds" without a verified markdown. The prior sender accepted a broad retailer department as value evidence. Mere low price and source category do not prove demand, savings, or resale value.
- The new authoritative public-send gate requires an actual high-value primary product class (including game consoles and physical controllers/gamepads, as distinct from games and digital credit), an exact hardware category leaf, or a flagged trustworthy reference showing a material price anomaly. Discovery/review remain broad so niche products are still explored. Existing offer identity, exact API price, buyability, stock and official-discount posting guards are unchanged; no new API calls, queues, or database tables.
- Public alerts no longer repeat misleading low-price scout route reasons. Negative regressions cover each owner-supplied false-positive class and gaming accessories; positives cover consoles, gaming controllers, laptops, tablets, and rare collectibles with source-backed comparison evidence. A platform name appearing in a game title is not enough to qualify as console hardware.
- Branch: `fix/walmart-extreme-value-gate-306`; PR: https://github.com/UglyGameFace/SniperPlug/pull/305. Earlier unrelated open PRs #200 (eBay) and #273 (movie Local mode) remain untouched.
- Current production acceptance is **not complete**: no Discloud deployment or real post/false-positive sampling has been verified, and a recognized high-value type alone is not independent proof of actual online demand or discount. After merge, inspect production rejection reasons and newly posted qualified items, track false-positive rate and missed premium items, and verify no ordinary digital goods leak into the public extreme lane. Do not claim market demand has been independently measured.

## Active task / outcome
**SP-WALMART-EXTREME-PRICE-295: restore detection and opt-in delivery for extreme exact-price Walmart leads without relabeling them as verified markdowns.**

User explicitly authorized a task switch on 2026-10-27:
`FORCE SWITCH: Fix Walmart missed-deal alerts in SniperPlug`
`Reason: Prioritize verified extreme-price discovery and delivery without weakening genuine discount verification.`

Base main: `b5fd137a945de415c5474a65f95c812432670599`; branch `fix/walmart-extreme-price-alerts-295`; issue #295.

## Scope / findings
- Walmart global discovery covers 265 keyword routes at two routes per minute with bounded/paged search; no ZIP/store context in Affiliate search. An outside screenshot of a $1 Samsung Galaxy S24 FE open-box demo is not proof that the API returned a matching item/offer or a buyable $1 price.
- Exact verification queue distinguishes official current price and item/offer/seller identity from a trusted reference price. Only `verified_markdown` enters global public fanout. Exact low-price items missing an API reference can remain `verified_no_reference` even when seller, offer, and current price were verified.
- Existing `low_price_scout` supports review-only leads; existing public scout/markdown quality boundaries must remain intact. Existing durable public post receipts and duplicate control must be reused; no parallel uncontrolled sender.
- Public health inspects at most 80 recent events; 71 were below 40% and nine quality-rejected. The 36,819 delayed retries need separate troubleshooting, but no proof these alone caused the exact missed phone.
- Existing static tests deliberately forbid generic auto-posted scout leads. New extreme-price mode must have explicit guarded eligibility, separate configuration, honest labeling, and regression coverage rather than weakening those safeguards.
- HP/Target, broad refactors, and source-specific localized Walmart offer fetching are out of scope. Honest limitation: nationally queried Affiliate prices do not prove store-local $1 deals.

## Status
**PR #296 merged to main** on October 27, 2026 at commit `78e5fcffb457ce7dc0fc1cbc764263e44c8f7068`.
The guarded opt-in Walmart implementation is integrated in GitHub, not yet verified as deployed to the live Discloud instance. No live Discord posting or actual $1 Samsung offer proof has been observed.
The active task stays open for safe deployment, explicit server opt-in, live alert validation, and final acceptance. The Discloud/Turso cutover remains suspended as preserved below.

## Oct 27 post-merge production-log evidence / same-task continuation
- Owner supplied production logs 16:23-16:40. Verifier batches often had 8/8 or 24/24 `api_price_missing` failures, and Product Lookup 6001 no-match; some batches did yield verified markdowns/no-reference offers. Upstream Walmart current-price coverage is incomplete; *do not* assert that a missing API price is an available bargain.
- Catalog routes 173-188 were on deep window 37-38 of 40 (some returning zero results). With 265 routes and two routes per minute, the old deep-only plan could go ~20 full passes before page 1 returned, making new products invisible for many hours. Discovery also paused at fresh exact backlog 14/12, 29/12, 39/12. Recurrent no-price work limits exact throughput.
- Same active Walmart task continued as **merged PR #297** at `21d69d0054a4ac0d26e4eee7a1ed6c0290848ef8` (branch `fix/walmart-fresh-catalog-pages-297`; based on main `7ebd2cb8b113608e6e09e561bfb5b19109311b97`). Preserved exact seller/offer/current-price and trusted markdown rules, 40% verified threshold, and existing DB backend.
- Narrowest direct correction: change the authoritative `catalog_pages_for_completed_passes` planner to always request fresh page 1 and rotate a single deep page 2-40 using the existing durable pass counter. Preserve two pages per keyword, concurrency, route count, state, no extra API calls or app workers. Adjust misleading page range display and test complete 40-page coverage.
- Verification: PR #297 and post-merge `main` checks were green on Python 3.11/3.12 (1503 tests each on PR), import smoke, compile, pip check; final three-file diff was reviewed for scope, conflicts, and secrets. **Still pending:** live Discloud scanner shows page `1,depth`, genuine retailer exact proof, Discord receipt and no duplicate posts. Walmart API may continue returning `api_price_missing`/6001, and user has not supplied the original offer ID.
- Movie ticket channel permissions are unrelated backlog and **must not be touched** under this task. Other existing app storage cutover remains suspended.

## October 27 production blocker: Turso mapping rows
Owner's Discloud logs confirm the prior fresh-page search change **is deployed** (`page window **1,4**/**40**`) and a normal verified Walmart fanout recorded **one actual public post** (16:59:34 logger prefix 15:59:34). At 17:03:35 `/walmart_extreme_alerts` failed with `KeyError: 0` in `set_extreme_alerts_enabled`. This is a real end-to-end activation blocker, not a hypothetical missed item.

Read-only inspection of the new feature identified every SQLite-index-only access in its opt-in/count, fanout guild list, cursor and candidate rows. Production Turso uses dictionary-like cursor rows. Branch `fix/walmart-extreme-row-compat-298` changes these reads to name-first with positional fallback consistent with existing Walmart health helpers, aliases the aggregate `enabled_count`, and adds a real SQL integration test with dictionary row_factory reproducing the error. **No change to discount, price proof, seller/offer, other cogs, or database backend.**

Acceptance requires green full CI on exact PR head, merge/push, live Discloud deployment of that merge, successful `/walmart_extreme_alerts enabled:true`, enabled state in `/autoscan_health`, and a real qualified exact-price alert/receipt. The original S24 FE $1 offer remains unproven from official Walmart evidence. No access to live Turso/Discloud is available via this chat.

## Oct 27 activation bug hotfix (PR #298, merged)
Latest owner-supplied live Discloud logs prove:
1. `page window **1,4**/**40**` after PR #297, meaning the catalog freshness fix is genuinely deployed.
2. Normal verified-deal Walmart fanout at 16:59:34 recorded `public guild checks/posts 4/1`; at least one regular Walmart alert was posted. This does **not** establish the optional extreme-price lane delivered.
3. `/walmart_extreme_alerts` failed at 17:03:35 with `KeyError: 0` because Turso cursor rows are name-keyed mappings, while the new opt-in code assumed numeric SQLite indexes.
4. PR #298 squash-merged into main as `0f6be9ec8cf55894a0fa3d9f55152e76ad0c2ad2`; corrected the aggregate count, enabled setting, opted guild list, cursor, and queue reads to use named column-first / positional fallback. Added an actual SQL dict-row regression across enable/disable, fanout, cursor, and dedupe; **1504 tests passed on both Python 3.11 and 3.12**, with import smoke green. No DB migrations or existing Discord settings changes.

**Live acceptance still pending**: latest PR #298 code needs to deploy in the existing Discloud app, with existing Turso/SQLite connection unchanged; user can repeat `/walmart_extreme_alerts enabled:true` and then `/autoscan_health` to verify saved state. Watch `sniperplug.autoscan.extreme_price` logs for real qualifying exact-item leads, posts and saved receipts. Do not claim the new extreme feed has sent a real alert until observed, and don't confuse a normal markdown fanout post with an extreme-price delivery. Persistent Walmart Affiliate `api_price_missing` and item 6001 no-match are ongoing source constraints; do not invent item prices or alter strong selected-offer proof.

## Next same-task slice: transparent extreme-price eligibility diagnostics
No new production logs after PR #298 were available at the start of this slice. A fresh read-only review showed `/autoscan_health` currently exposes only the enabled/disabled setting and cannot distinguish no qualifying API items from sender/backlog failure. This is a real observability gap, not proof of a new delivery regression.

**Merged PR #299** at `b65f87767b69b42d1b2ed53262e819075ee5a9a1` adds a **read-only, capped 60-row, 15-minute queue inspection** via the existing `walmart_extreme_price_fanout.py` and existing `/autoscan_health` field. It shows sampled exact-verified sub-$10 queue rows in the sender's statuses, how many pass full primary-product/stock/offer/price identity gates, and how many qualified rows are still beyond the global delivery cursor. It uses named-column-first Turso/SQLite compatible reads and parses the same durable snapshots as the sender; no new API requests, worker, cursor updates, mutation, increased HTTP traffic, or relaxation of source evidence. A SQL mapping-row regression checks that accessories are not counted as qualified.

**Validation:** all three checks green on PR #299; **1505 passed on Python 3.11 and 3.12**, import smoke passed, post-merge `main` checks to verify. **Outstanding acceptance:** verify the read-only diagnostic with a production `/autoscan_health` screenshot and separately observe real extreme-price alert delivery and receipt. The diagnostic counts a bounded recent candidate sample, not Walmart's entire live catalog nor confirmed Discord posts. Keep DB migration and movie-ticket source out of scope.

## Oct 27 live enabled-state screenshot and reason-code diagnostic

The owner supplied a live `/autoscan_health` screenshot for their Walmart channel after PR #299. Grounded findings:
- Global Walmart enrollment yes; saved public alert setup healthy; **extreme exact-price alerts enabled**. This confirms PR #298's Turso opt-in crash was resolved and PR #299's health panel deployed.
- Latest 80 24-hour exact fanout decisions: **0 posts to this server**, **72 below its 40% threshold**, **8 quality/proof rejects**, no duplicate/delivery failure reported. This is not an exhaustive count of all 24-hour events.
- Extreme alert last-15-minute capped diagnostic: **2** verified-price queue rows at $10 or less, **0** eligible primary-product/seller/stock/offer/price matches, **0** beyond delivery cursor. No proof these two were actual valuable phones, and no reason breakdown previously existed.
- Exact queue `total 43,999`, `due now 30`, `delayed transient retries 38,546`, `verified 3,808`. Do not confuse delayed retries with the immediately due workload.
- The screenshot does not prove a recent qualifying extreme-price alert was missed; instead, it exposes the remaining lack of visibility into **which eligibility guard** excluded each priced row.

**Merged PR #300** at `d20b7e71cf9ac5d659eb195e30aebfbed8924056` (branch `fix/walmart-extreme-rejection-breakdown-300`) uses one shared `extreme_rejection_reason` function as the *sole* definition of sender eligibility. It preserves exact offer/seller/item proof and the existing $10 cap, and reports aggregate, coarsely labeled first-rejection reasons among the existing bounded 60-row sample in `/autoscan_health`. This avoids raw item IDs, per-offer records, additional API calls, new tables, or unverified deal posting. Targeted unit tests assert identical sender decisions and expected reasons for a real-primary-product fixture, accessories, nonprimary products, unavailable items, missing offer proof, price mismatch, and Turso-style dict rows.

**Validation:** all three PR checks passed, including **1506 tests** each on Python 3.11 and 3.12 and import smoke; check updated production health screenshot after deployment to learn what blocked the two actual candidates. A real extreme-price Discord alert with saved receipt remains unobserved; retain current protections and do not infer that an API item is buyable from a catalog price alone.

## October 27 silent-delivery-loss audit: empty live-route reconciliation
The user requested continued hardening after the already-merged PR #300. A source audit of `fanout_recent_extreme_walmart_prices` found a demonstrable edge case: the optional feed checks an opted-in guild set and only performs live delivery-route reconciliation when an eligible exact-price card is present. If that reconciliation returns `guilds=()` temporarily, the old Python `for guild_id in enabled: ... else: next_cursor=(verified_at,item_id)` silently advanced the global Walmart extreme-price cursor despite sending to zero recipients. The eligible item would be lost from the optional feed for all later route recoveries.

**Merged PR #301** as `a703961abbe4787a29e13c1fbd6b676b8167d373` (branch `fix/walmart-extreme-no-route-cursor-301`) now stops on the first eligible offer with no live opted-in recipient, logs a bounded warning, and **preserves the cursor** so it can be delivered if the route recovers within the existing 15-minute freshness window. It does not increase Walmart HTTP requests, create new background loops, change database schemas, alter source pricing evidence or verified markdown thresholds. Existing durable receipt/dedupe still handles actual deliveries. A real SQLite integration regression tests the empty-live-guild outcome, verifies the unchanged cursor, then simulates the guild becoming live and receives exactly one alert. All three PR checks green with **1507 passed** on Python 3.11 and Python 3.12, plus successful import smoke; safe squashed merge into main.

Limitations: if a guild remains offline after the 15-minute source-evidence freshness window, this design cannot legitimately replay stale prices. Separate per-guild reliable replay would require stronger offer re-verification and more invasive delivery-state design; do not silently send stale prices. Live Discloud rollout and a genuine optional-feed alert remain to be verified.

## Walmart multi-guild reliability slice: per-guild recovery outbox (October 27)
A read-only audit after merged PR #301 found the remaining partial-live-guild case: when guild A is live and receives an eligible Walmart exact-price alert, the single global cursor advances; a separately opted-in guild B missing from Discord's live-guild reconciliation has no sender attempt, so no receipt exists and guild B loses that offer even if it reconnects seconds later. A previous no-recipient-only guard could not solve this mixed-recipient scenario.

**Merged PR #302** at `4e33eebb0ac0b744904222f17948c4c72815d5b9` (branch `fix/walmart-extreme-guild-backlog-302`) adds a narrowly scoped **expiring, durable per-guild missing-recipient outbox** as a new dedicated table. It stores a record only when an official exact offer has already passed existing primary-product/offer/seller/current-price verification but an opted-in destination is temporarily absent from the live route set. Records are inserted **before** the shared cursor is acknowledged. On recovery, only guilds which remain explicitly opted-in and currently enrolled in live Walmart alerts are eligible; delivery reuses `maybe_post_public_deal_cards` with existing durable receipt/dedupe. Replay verifies the saved exact official snapshot and verified timestamp still match the authoritative current queue row and that evidence is less than 15 minutes old; changed/expired evidence is dropped rather than misreported as a current checkout price. Explicit opt-out cancels pending rows. Avoid unbounded writes using a maximum 128 absent guilds per qualified offer, 32 replay attempts per cycle and batched `IN` queries; if an offer exceeds the missing-guild cap, fail closed with the cursor unchanged, never silently advance.
No new Walmart API fetches, scheduled workers, verified-markdown filter changes, or old Discloud/Turso migration.

The existing `/autoscan_health` field now includes the **specific server's unexpired pending recovery count** so the global cursor alone does not mislead owners into believing delivery is complete. Regression uses real SQLite with Turso-like mapping rows: guild A live, guild B missing, A receives once, B is durably queued then receives once after live recovery even though global cursor moved; source price/verification changes prevent stale replay, and explicit opt-out deletes tickets.

Owner opt-out and the extreme-price fanout now share the existing async worker lock so a completed opt-out cannot race an already selected in-flight send. Full PR CI passed **1510 tests** on Python 3.11 and 3.12 plus import smoke; all three checks green on the exact merged PR head. This is a deliberately small bounded outbox, not unlimited exactly-once delivery across arbitrary Discord outages. Live deployment, receipt and source coverage remain to be verified. Keep HP/Target, movie-ticket warnings and production database cutover outside this task.

## October 27: PR #302 regression exposed during replay-path audit
Follow-up source review demonstrated a concrete mixed-state bug after PR #302. When the new `_replay_missing_guild_deliveries` found at least one pending ticket but zero eligible live recipients, it returned `LiveGuildLoadResult(guilds=())`, not `None`. The next new-offer loop treated this cached empty list as already checked and skipped the previously added no-live-recipient guard inside `if enabled is None`; the Python empty `for/else` then advanced the shared fanout cursor despite zero destinations. This could silently lose newly eligible exact-price items while an older missing-guild ticket existed.

**Merged PR #303** as `250c4ab0b285a1bb7e6fd56c533443ae44f1f996` (branch `fix/walmart-replay-empty-live-guild-303`) moves the no-recipient fail-closed guard outside the conditional fetch so **both** fresh reconciliation and replay-cached reconciliation must contain a live opted-in recipient before any qualified offer may be acknowledged. No new table, Walmart request, status, price rule, or sender. Regression tests use real SQL with Turso dictionary rows and two officially verified candidate snapshots; a pending ticket from item A creates an empty replay result while new item B waits, then recovery sends A and B only once and advances safely. PR #303 CI passed on final exact head: **1511 tests each on Python 3.11/3.12** and import smoke; merge completed. Production deployment and real optional alert/receipt remain unverified.

## Implemented changes
- New `sniperplug/services/walmart_extreme_price_fanout.py`: separate 15-minute freshness window, bounded 40-row cursor, selected exact statuses that lack a trusted markdown above normal thresholds, sub-$10 current API price, verified seller/offer/item identity, buyable status, recognizable primary product, accessory exclusions and honest no-reference labeling.
- Per-guild explicit opt-in stored by exact-text Discord guild ID, default OFF; `/walmart_extreme_alerts enabled:true`; health dashboard shows opt-in status.
- `sniperplug/cogs/global_auto_scan_runner.py`: fanout after normal verified markdown/DM fanout but before queue maintenance. Guild-wide live reconciliation is avoided entirely when the optional setting is empty, and otherwise deferred until an eligible lead appears.
- `sniperplug/services/public_deal_posts.py`: one opt-in exact-price sender guard; original verified-markdown, manual scout, and DM behavior preserve defaults. Uses existing channels, feedback, durable reservation, duplicate receipt and short scout-style dedupe; no secondary Discord publisher.
- `tests/test_walmart_extreme_price_alerts_295.py`: $1 S24 FE no-reference eligibility, accessory/mismatch/unavailable rejects, on/off, durable cursor retry, actual shared sender and forged proof rejection.

## Validation / results
- Base `main`: b5fd137a945de415c5474a65f95c812432670599.
- Final validated PR implementation head: `314767247e0000ffbb90744fa5433bde23f2e6bc`.
- GitHub Python tests on Python 3.11: **1503 passed, 1 warning**.
- GitHub Python Check on Python 3.12: **1503 passed, 1 warning**; 37-module / 22-symbol import smoke passed.
- Separate import smoke passed; `pip check` and `compileall` passed on both CI jobs.
- Existing generic scout-block and verified-markdown gates still pass their regressions; the opt-in true sender path has a direct integration regression with synthetic $1 offer and tests the forged-card fail-closed path.
- Reviewed merged PR diff for file scope and safety. No shared code was deleted. Existing code paths stay on their old defaults.
- All three PR checks passed on exact final PR head; subsequent main push checks must be verified separately. The only post-merge change here is task-record documentation.

## Unvalidated / blockers / risks
- Walmart raw `availableOnline` text can be misread by the existing provider using `bool(value)`; the new extreme-price lane explicitly rejects negative source flags and not-available stock. This narrowly guarded known source issue without changing other provider semantics.
- The original reported $1 Galaxy S24 FE offer's true item ID, exact seller variant, regional checkout, and appearance in the Walmart Affiliate API are unproven. API search has no store ZIP context, so store-only deals cannot be promised.
- No live **extreme-price** end-to-end alert has yet been observed; do not call the fix deployed or production accepted. Keep PR draft until operational acceptance or verified readiness to merge.
- The large existing delayed retry population remains a source/API evidence limitation, not bypassed. This task preserves the current exact verification conditions; it does not claim to solve all missed Walmart listings.
- Expected operator acceptance: preserve current DB snapshot/backend; merge and deploy verified code safely; run `/walmart_extreme_alerts enabled:true` once in the chosen server; check `/autoscan_health` and the exact-price log/durable receipt using a genuine current Walmart API offer. Do not infer a verified percent markdown from the new lead.


## Planned execution path
Walmart API catalog -> existing global exact queue -> official exact verified candidate + durable queue state -> dedicated bounded extreme-price candidate lane (no trusted reference needed) -> per-guild explicit opt-in -> existing public-post channel, receipt/dedupe and failure handling; verified markdown and DM fanout unchanged.

## Definition of done
- Verify source evidence and avoid false proof/percentage claims; be explicit about price, condition, buyer availability.
- Opt-in, precondition gating, bounded cursor, dedupe, no double-post, proper failure treatment and diagnostic visibility.
- Targeted and relevant tests, Python 3.11/3.12 CI, import/compile, code/secret/conflict/diff scan, and deployment acceptance when possible.
- Review obsolete/conflicting code in affected area, preserve other user work. Record blockers and remaining validation truthfully.

## Suspended work: SP-LOCAL-SQLITE-DISCLOUD
The prior record, findings, code changes, CI evidence, branch/PR status, blockers, runbook, and next step are reproduced verbatim below to resume without lost context. **Do not execute this migration while #295 is active.**

---
# Active Task

## Active task / outcome
**SP-LOCAL-SQLITE-DISCLOUD — move canonical production storage from Turso to local Discloud SQLite.**

Outcome: use the existing local SQLite backend inside the canonical SniperPlug Discloud Diamond app, eliminate Turso row-read/write quota dependency, and keep HP/Target capable of sharing the same authoritative data by running them as supervised tasks inside the canonical app instead of separate Discloud applications.

Base main: `8b34e316977ac0a4b754b8e751d8a73377f03484` (PR #292 merge).
Branch: `infra/local-sqlite-discloud-293`.
Draft PR: #293.

Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

## Status
Implementation and executable validation are complete on the migration code. The branch includes the fail-closed missing/empty-database guard, embedded watcher/local-SQLite architecture, migration installer, backup path and deployment runbook. PR #293 is now marked ready for review. Live Discloud cutover and production acceptance are still pending because this chat has no Discloud control-plane connector.

## Findings / root cause
- Turso blocked the account after the monthly free allowance was exceeded: about 530M rows read and 11.16M rows written were visible in the supplied production dashboard.
- Production analytics showed sustained high database activity rather than one isolated query: roughly tens of millions of row reads and around one million writes per day after the Turso-backed production workload became active.
- SniperPlug already has a real local `aiosqlite` path in `Database.connect()` with WAL and foreign keys; Turso is selected only when Turso/libSQL credentials are present.
- `create_runtime_database()` is already shared by the canonical bot plus the HP and Target watcher entrypoints.
- HP and Target were originally deployed as separate Discloud apps specifically because separate apps cannot share the canonical app's local SQLite file. Simply removing Turso credentials from all three apps would therefore create three isolated databases and break shared state/fanout semantics.
- The correct local architecture is one canonical Discloud application. Embedded HP/Target workers use independent SQLite connections to the same WAL file so their transactions are not interleaved on one connection.
- A missing local database is dangerous because SQLite normally creates it automatically; explicit local production mode now requires an existing non-empty database before connection. A deliberately fresh install needs the one-time `SNIPERPLUG_LOCAL_SQLITE_ALLOW_EMPTY=true` override.
- HP currently has a separate production HTTP/PDP timeout incident. The local-database task does not guess at that unrelated transport root cause, so the production runbook keeps embedded HP disabled until that incident is handled under its own task.

## Execution path
1. `main.py` -> `sniperplug.process_runtime.run()`.
2. `ProcessIsolatedSniperPlugBot` creates the canonical runtime database through `create_runtime_database(settings.database_path)`.
3. With no Turso/libSQL credentials, the existing database factory falls through to local SQLite at `DATABASE_PATH`.
4. `SniperPlugBot.setup_hook()` initializes the schema and normal bot services.
5. `ProcessIsolatedSniperPlugBot.setup_hook()` configures the local SQLite connection for bounded writer contention, starts verified local snapshot backups, then optionally starts embedded HP/Target watcher supervisors.
6. Each embedded watcher calls its existing watcher runtime with `require_remote_database=False`, opens its own connection to the exact same `DATABASE_PATH`, initializes compatible schema idempotently, and runs its existing service loop.
7. The canonical bot remains the Discord publisher and consumes the same durable verified-retailer event tables.

## Changes
- `sniperplug/process_runtime.py`
  - explicit `SNIPERPLUG_LOCAL_SQLITE=true` production assertion;
  - fail closed if local mode is combined with any Turso/libSQL credentials;
  - fail closed before SQLite can create a missing/empty production DB, with a one-time explicit empty-bootstrap override only;
  - supervised embedded HP and Target watcher tasks;
  - watcher settings forced to the canonical `DATABASE_PATH` and local-database validation mode;
  - embedded services require a confirmed `backend=sqlite`;
  - coordinated cancellation before canonical DB shutdown;
  - local backup scheduling.
- `sniperplug/storage/local_sqlite.py`
  - canonical bounded SQLite `busy_timeout` configuration for the independent writer connections.
- `sniperplug/services/local_sqlite_backup.py`
  - SQLite online backup API snapshots;
  - post-copy `PRAGMA integrity_check`;
  - atomic publication and bounded retention;
  - remote-backend no-op behavior.
- `sniperplug/hp_watcher/app.py` and `sniperplug/target_watcher/app.py`
  - configure local SQLite contention before schema work;
  - log the real backend instead of always claiming process-isolated Turso.
- `scripts/install_sqlite_snapshot.py`
  - integrity-check a supplied SQLite export;
  - verify core SniperPlug table identity;
  - preserve an existing local DB through SQLite's online backup API;
  - stage and atomically install the production snapshot;
  - remove stale WAL/SHM sidecars;
  - final integrity validation.
- `discloud.config`
  - canonical app RAM raised from 512 MB to 1024 MB for the consolidated bot/watcher runtime while staying within Diamond capacity.
- `discloud.hp-watcher.config` / `discloud.target-watcher.config`
  - explicitly marked legacy standalone remote-database deployments so they are not accidentally run beside local embedded workers.
- `docs/DISCLOUD_DEPLOY.md`
  - canonical Diamond/local-SQLite migration and recovery runbook;
  - required environment changes;
  - watcher ownership rules;
  - backup-before-redeploy discipline and production acceptance checks.
- `tests/test_local_sqlite_discloud_runtime.py`
  - snapshot creation/integrity regression;
  - remote-backend no-op regression;
  - SQLite writer timeout regression;
  - safe migration installer regressions;
  - embedded watcher/local-mode architecture guards;
  - Discloud deployment-surface guards.

## Validation / results
- Exact migration-code head `600d3ec94e5d326805199b784cd7df04240d32e2` passed both hosted full suites:
  - Python 3.11: `1492 passed, 1 warning`;
  - Python 3.12: `1492 passed, 1 warning`.
- `pip check` passed.
- compileall passed.
- import smoke passed (`37 modules, 22 symbols`).
- PR is 25 commits ahead / 0 behind production `main`; raw GitHub state reports `mergeable=true` and `rebaseable=true`.
- Final diff inspection found no conflict markers, committed secrets, breakpoint/debug artifacts, or unrelated file scope. The apparent secret hit is the documented placeholder `TARGET_REDSKY_API_KEY=your_target_redsky_key`; CLI `print()` calls are intentional migration-script output.
- One earlier exact-head run exposed two static regressions only (lost Python 3.14 rationale comment and case-sensitive deployment-doc assertion); both were corrected and the subsequent full suites passed.

## Final exact-head validation required after this task-record-only commit
- targeted `tests/test_local_sqlite_discloud_runtime.py`;
- existing database/runtime tests, including `test_libsql_process_connection.py` and local SQLite storage coverage;
- HP and Target watcher configuration/runtime regressions;
- verified-retailer event/fanout regressions;
- Walmart queue/storage regressions relevant to SQLite concurrency;
- compileall;
- import smoke for `main.py`, HP watcher and Target watcher;
- `pip check` / repository dependency validation;
- full pytest suite using repository conventions;
- PR exact changed-file scope and diff review;
- conflict-marker / accidental secret / generated-file scan;
- final exact-head SHA validation;
- rerun exact-head CI once after this `ACTIVE_TASK.md` record update; no implementation files changed after the green `600d3ec...` run.

## Discloud backup inspection — October 4, 2026
A fresh user-supplied Discloud app backup was inspected before any production cutover.

Findings:
- the archive contains `data/sniperplug.sqlite3` plus its WAL/SHM sidecars and current application logs;
- that SQLite database passes `PRAGMA integrity_check`, but its newest stored activity is from May 30, 2026 and it has only 15 user/system tables, so it is a stale pre-Turso/local fallback and is **not** acceptable as the production migration source;
- the archive contains no `sniperplug.turso-replica.sqlite3` or other embedded-replica database file;
- production logs show Turso read/write quota blocks from September 30 through October 1 around 08:05, but live Turso-backed application reads continue successfully on October 3–4;
- because remote reads are available after the quota reset while the Turso UI remains unusable/full, the migration now includes `scripts/export_turso_snapshot.py`, a read-only in-app recovery exporter that copies current Turso state to a verified local SQLite snapshot without issuing remote writes.

## Production migration blockers / risks
- The existing Turso production data must be captured as a current SQLite snapshot before cutover. Starting an empty DB or using the stale May local fallback would lose current guild settings, DM preferences, queues, histories and watcher state.
- Turso's UI/provider export is unavailable while the account is full, but the October 4 Discloud backup proves live remote reads resumed after the October 1 quota reset. The read-only exporter should be attempted before waiting for provider support/reset; if remote reads become blocked again, no safe current snapshot can be created from Turso until reads return.
- ChatGPT has GitHub access here but no connected Discloud account-management tool, so live Discloud environment-variable changes, app stop/start, file upload and app-backup operations require the user's Discloud control plane unless a compatible connector becomes available.
- Discloud runtime-file retention across every deployment path is not assumed. Off-host Discloud app backups remain mandatory before production redeploys.
- HP transport/PDP failures are a separate backlog item and embedded HP stays disabled in the runbook until that root cause is repaired.

## Suspended / backlog
- PR #273 / `SP-MOVIE-TICKETS-RECOVERY` remains suspended and untouched.
- PR #200 eBay watcher remains suspended and untouched.
- HP transport/PDP timeout/403 incident remains backlog after this database migration task.
- Turso query-efficiency cleanup is no longer required to avoid Turso billing after local cutover, but obviously wasteful SQL remains eligible for a later performance task if local runtime metrics justify it.

## October 4 Discloud console follow-up
After PR #293 merged and deployed, running the documented recovery command directly in the Discloud console failed before connecting to Turso: `ModuleNotFoundError: No module named 'sniperplug'`. Root cause: executing `python scripts/export_turso_snapshot.py` sets `sys.path[0]` to the `scripts` directory, so the sibling `sniperplug` package was not importable. Follow-up branch `fix/discloud-exporter-import-294` adds the same repository-root bootstrap already used by `scripts/smoke_imports.py` and a subprocess regression covering the exact direct CLI launch shape.

## October 4 Hrana recovery follow-up
The next live Discloud export attempt reached Turso but failed on `PRAGMA query_only=ON` with Hrana `SQL_PARSE_ERROR`. Hrana rejects that SQLite connection-level pragma. The exporter now enforces read-only behavior locally with a strict SQL allowlist, allowing only SELECT, BEGIN/rollback, and `PRAGMA table_info(...)`; any write or unsupported pragma is rejected before the source connection sees it. The incomplete `.partial` export remains invalid and will be replaced on the next run.

## Next step
The read-only Turso exporter is validated on exact head `27de7d21ec890966625472634f5c381142937b26`: Python 3.11 and Python 3.12 full suites each passed `1495 passed, 1 warning`, and import smoke passed. PR #293 may now be merged/deployed **without changing the existing Turso/libSQL environment**, which keeps production on the current remote backend. After deployment, run `python scripts/export_turso_snapshot.py --output ./migration/sniperplug-turso-export.sqlite3` inside Discloud while reads still work. Do not remove Turso credentials or enable local mode until that command finishes successfully and the completed snapshot is installed.


## November 2, 2026: real-world $1 tablet report and category-wide extreme-price remediation (PR #304)

- Owner supplied an X screenshot from TheDealsGuy advertising a $1 Samsung Galaxy Tab S11 256GB Walmart product; owner reports many successful public checkouts. This is a priority real-world miss report. No official Walmart item ID, exact seller/variant/ZIP-scoped API response, SniperPlug item queue trace, or Discord delivery receipt has yet been retrieved. Do not claim the deal was fake, or claim the bot specifically rejected that offer without a per-item trace.
- Inspected main and found a **proven general blind spot**: the opt-in extreme-price public sender required a closed list of primary-product name regexes. An exact-verified new product model could be rejected as `not_recognized_primary_product` even when price, seller, stock, item ID and checkout proof were valid.
- **Open PR #304** (`fix/walmart-galaxy-tab-extreme-304`): replace closed model-name eligibility with Walmart-origin categoryPath, generic durable product type, trustworthy price-reference evidence, and a compatibility fallback for old category-less device records. Accessory/dummy filtering remains; nonproof/price mismatch/stock/offers still fail closed. No fake MSRP or markdown percentage.
- Also boost source-identified potentially valuable $1-$10 catalog rows in the *existing* exact queue and post fully verified opt-in extreme alerts before normal markdown fanout, without new workers, HTTP requests, migrations, or lowering markdown rules. Source-backed category classification allows previously unknown brands/products without requiring code updates per new model.
- Exact code head `564a2d5f4c9b51315eac77aa8d80fb98eed39dfb`: GitHub Actions Python 3.11 `1513 passed, 1 warning`; Python 3.12 Python Check green; import smoke green. The next documentation-only commit must also pass exact-head CI. The PR is **not merged or live-deployed** as of this entry.
- **Important unsolved discovery-speed problem**: around 265 keyword routes at two routes per minute means at least ~133 minutes per full route rotation, more when source backpressure/availability intervenes. New $1 listings can disappear before discovery; a priority queue cannot recover a product never retrieved. Walmart Affiliate price omissions and store-specific offers remain additional blind spots.
- Next production outcome after PR review/merge: measure item discovery-to-verified-to-Discord latency, locate screenshot's Walmart item/offer IDs when obtainable, add source/quota-verified rapid price-anomaly discovery that does not starve normal category coverage, and verify real delivered alerts. No claim of being faster than competitors until production data shows that.

## November 2 follow-up: claim-safe faster Walmart discovery in PR #304

- Same open PR now also addresses the observed 265-route catalog freshness bottleneck. The prior two-route-per-minute plan made four API requests: pages 1 and rotating depth for both routes. A theoretical full first-page sweep was at least 133 minutes before source pauses or exact-queue backpressure.
- New global claim plan performs **three page-one routes plus one fairly rotated deep-page request each minute**. Still four API searches per batch, the same concurrency/request coordinator, one global worker, no new API credentials or database schema, and the existing exact-detail/stock/offer/price proof is unchanged.
- Theoretical uninterrupted first-page sweep improves to ceil(265/3) = **89 minutes**, about **33% shorter** than ceil(265/2) = 133 minutes. This is NOT a measured production latency or a claim SniperPlug has beaten another bot. Discloud source throttling, active queue backpressure, and retailer-specific/local-only offers may extend or defeat discovery.
- Deep-page selection derives deterministically from the durable catalog cursor and pass count. Across tests for pool sizes 3,4,5,6,7,8,265, each route receives page one on every revisit and each page 2 through 40 once per depth cycle; a retry reuses exactly the same four requests. This is slower depth rotation than the former two-pages-for-every-route mode, an intentional tradeoff for faster fresh discovery.
- Updated canonical claim default from four to three routes and added complete planner/restart/fake-provider/request-budget/route mismatch regressions. No parallel scan or dropped cursor on exception. Manual discovery callers without global claims retain the old two-page behavior.
- Code head `b4da3d54f508a8cf71288b21b599f43a73bff52a` validated: Python 3.11 **1524 passed, 1 warning**; Python 3.12 **1524 passed, 1 warning**; import smoke successful; pip check and compile/check successful. The final status-doc-only commit needs exact-head CI.
- Remaining acceptance: PR review/merge, actual Discloud deployment of main, source and destination health, live item-level discovery/verification/delivery timestamps, sample retail $1 offer proof, and monitored source quota/backpressure. Do not claim production speedup or guaranteed real-deal capture until verified.


## November 2 continuation: database quota/storage safeguards in PR #304

- User explicitly required no repeat of the previous fast database/quota blowout. Re-audited current claim, discovery, queue, exact verification, fanout, and pruning paths before considering merge.
- **Verified no multiplicative scanner:** only one global scanner, not per-guild; three page-one + one deep HTTP requests retain the previous **four search requests per 60-second cycle**. Querying more fresh routes does not intrinsically increase API or storage operations.
- **Fixed real redundant DB traffic:** `ensure_global_catalog_state()` formerly executed `CREATE TABLE IF NOT EXISTS`, `INSERT ... ON CONFLICT DO NOTHING`, and `COMMIT` on every claim, completion, and state read. It is now cached once per live DB connection, protected by an async lock, retries after failures, and reinitializes for new connections. Tests use a counting real SQLite connection and cover concurrency, claims, completion, repeated reads, reconnect and failed initialization.
- **Hard per-batch ingress ceiling:** global discovery refuses configurations exceeding four HTTP search requests per claim; each provider response is capped to the requested `RESULTS_PER_PAGE` (25), so no more than **100 catalog candidates per minute** enter deduplication/queue, even if an upstream provider unexpectedly overreturns. Test asserts these caps under deliberately bloated responses. The existing bulk writer still batches in chunks of 55, persists only new/materially changed/stale items, and skips DB commits for repeated unchanged listings. Worker quotas/priority and 14-day expiry stay unchanged.
- **Important remaining risk:** `QUEUE_MAX_ROWS=50_000` is a soft post-fanout maintenance target, NOT a hard admission cap. Overflow cleaning currently deletes at most 100 rows per 5 minutes while under sustained overflow, versus up to 100 *new* item IDs per minute in a pathological all-unique catalog. Also, the overflow selector can scan a wide/expensive slice of the queue. Under prolonged all-unique intake, storage and database read costs can still grow too fast. Do **not** claim no storage blowout is guaranteed and do **not** treat this as production quota proof.
- Before deploying broadly, implement or verify a proper quota-aware high-watermark/retention admission policy with cheap indexed accesses, priority for potentially valuable fresh deals, and live DB storage/row-read telemetry. Ensure high priority $1 opportunities are not suppressed by stale low-quality rows. Real Discloud/Turso status, DB size/usage and actual retailer/source receipts are unverified via the GitHub-only connection.
- These guard changes remain in the same PR #304. All checks were green on code head `b534379ad6a98b0888525461a83b5ef856e3c0b6`: Python 3.11 and 3.12 tests, Python Check, import smoke. A final documentation-only head needs CI rerun.


## November 2 follow-up: expired/unneeded deal release and bounded quota cleanup (PR #304)

- The user required that SniperPlug release unnecessary/expired deals instead of filling the Turso database again. The preexisting queue `QUEUE_RETENTION_DAYS=14` and `QUEUE_MAX_ROWS=50_000` existed, but the cleaner only removed 100 rows per 5 minutes under pressure and its old overflow query sorted the full table with `CASE status ... OFFSET 50_000`. At maximum 100 *new* catalog IDs per minute, that retention process could fall far behind even without additional HTTP calls.
- Modified the authoritative **post-fanout bounded cleaner**, not discovery or the exact worker, so live posting and exact verification remain ahead of maintenance. It now reclaims unseen/expired rows after 14 days, uses the indexed `last_seen_at` search, and probes total row count at most once every 30 minutes (fast `COUNT(*)` without a predicate) to estimate overflow. During backlog it releases up to **100 older rows per minute**, with 30-minute probe budget capped at 3000, rather than a huge one-off DELETE.
- Stale and overflow candidate lists are deduplicated and counted together so an expired row cannot cause over-deletion. It does not evict recently discovered items or rows verified in the last 90 minutes; actively verifying leased rows are protected. The final DELETE repeats the freshness and active-lease guards so a race does not drop newly verified offers. Short-lived opted-in alert proof and the separately persisted per-guild replay outbox remain intact.
- Existing item-ID deduplication, bulk write suppression (unchanged rediscovery gets zero queue writes), 24-hour heartbeat, and 14-day queue retention are preserved. The scanner's four HTTP requests/minute and 100 raw provider-candidate/minute ceiling are unchanged.
- Logs now separate stale-expired versus oldest-overflow cleanup counts, so production can show actual reclamation rather than just a configured policy. The cleaner deliberately retains a **soft** 50k target, not a transactionally enforced exact cap: 30-minute probe lag and active/fresh protections can allow temporary overflow, and extra unique-product writers could change the rate. There is no live DB-size/row-read quota proof available from the GitHub-only connection.
- Added real SQLite regression scenarios covering expired/unnecessary rows, over-limit evictions, active leases, recently checked verified deals, below-limit no-op, and <=100 deletions per run. Previous tests for old cleaner behavior were updated to account for safety predicates. **Must check exact final code head CI and real Turso/Discloud cleanup telemetry after merge.** Do not represent mere unit tests as production storage protection.

