# Active Task

## Active task / outcome
**SP-LOCAL-SQLITE-DISCLOUD — move canonical production storage from Turso to local Discloud SQLite.**

Outcome: use the existing local SQLite backend inside the canonical SniperPlug Discloud Diamond app, eliminate Turso row-read/write quota dependency, and keep HP/Target capable of sharing the same authoritative data by running them as supervised tasks inside the canonical app instead of separate Discloud applications.

Base main: `8b34e316977ac0a4b754b8e751d8a73377f03484` (PR #292 merge).
Branch: `infra/local-sqlite-discloud-293`.
Draft PR: #293.

Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

## Status
Implementation and executable validation are complete on the migration code. The branch includes the fail-closed missing/empty-database guard, embedded watcher/local-SQLite architecture, migration installer, backup path and deployment runbook. Live Discloud cutover and production acceptance are still pending because this chat has no Discloud control-plane connector.

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

## Production migration blockers / risks
- The existing Turso production data must be exported/downloaded as a SQLite snapshot before cutover. Starting an empty DB would lose current guild settings, DM preferences, queues, histories and watcher state.
- The current Turso account is read-blocked, so export availability must be confirmed. The code cannot manufacture a production snapshot while the provider refuses reads.
- ChatGPT has GitHub access here but no connected Discloud account-management tool, so live Discloud environment-variable changes, app stop/start, file upload and app-backup operations require the user's Discloud control plane unless a compatible connector becomes available.
- Discloud runtime-file retention across every deployment path is not assumed. Off-host Discloud app backups remain mandatory before production redeploys.
- HP transport/PDP failures are a separate backlog item and embedded HP stays disabled in the runbook until that root cause is repaired.

## Suspended / backlog
- PR #273 / `SP-MOVIE-TICKETS-RECOVERY` remains suspended and untouched.
- PR #200 eBay watcher remains suspended and untouched.
- HP transport/PDP timeout/403 incident remains backlog after this database migration task.
- Turso query-efficiency cleanup is no longer required to avoid Turso billing after local cutover, but obviously wasteful SQL remains eligible for a later performance task if local runtime metrics justify it.

## Next step
Confirm the task-record-only head remains green, then mark PR #293 ready for review. Do not perform the live cutover until a production snapshot is available in Discloud; after merge, install the verified snapshot, remove Turso/libSQL credentials, enable explicit local mode, keep HP disabled, verify Target and bot state, then retire Turso only after acceptance.