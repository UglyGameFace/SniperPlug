# Active Task

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
- No live Discloud/Discord end-to-end alert has yet been observed; do not call the fix deployed or production accepted. Keep PR draft until operational acceptance or verified readiness to merge.
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