# Active Task

## Task
Perform a complete execution-path audit of the SniperPlug Discord bot and its integration with `UglyGameFace/SniperPlug-Site`. Repair confirmed discrepancies only after inspecting real callers, tests, guards, compatibility layers, storage, configuration, and deployment behavior.

## Status
Audit active. Database/provider/autoscan execution paths are under review. No completion claim until implementation, targeted tests, regression checks, compilation/static validation, cleanup, conflict inspection, and bot/site integration validation pass.

## Confirmed findings
- Bot startup registers `CachedWalmartProvider(self.db, WalmartProvider(configured=False))` even though Settings exposes Walmart credentials and `WALMART_PROVIDER_ENABLED`; runtime wiring and configuration disagree.
- `CachedWalmartProvider` also defaults its inner provider to `WalmartProvider(configured=False)`, creating a second disable-by-default compatibility path.
- The active `native_auto_scan_runner.AutoScanRunnerCog` inherits the legacy `auto_scan_runner.AutoScanRunnerCog`; the legacy module is therefore live runtime code and cannot be deleted as dead code.
- The native runner can publicly post review/scout fallback cards, while the inherited legacy policy explicitly states scout/review leads must stay private. The active path and policy are contradictory.
- Turso/libsql serializes individual execute calls, but database workflows generally execute and commit statement-by-statement with no transaction abstraction. Multi-write operations can be partially committed after a mid-flow failure.
- `_split_sql_script` splits schema text on every semicolon. It is unsafe for future triggers or SQL bodies containing internal semicolons.
- Database tests prove statement serialization only; they do not test rollback, atomic multi-write workflows, reconnect behavior during transactions, or partial-failure recovery.
- CI compiles and smoke-imports but does not run the complete pytest suite.
- Storage maintenance blocks `setup_hook` before ready.
- Setup self-heal sets its one-shot flag before success, preventing retry after a failure.
- Broad exception handling appears across runtime services and must be classified into intentional isolation versus swallowed failures. Walmart cache, identity, observation, scan-run completion, and query-memory failures are currently swallowed or reduced to weak warnings.
- Dependencies remain unpinned, allowing deployment behavior to change without a repository change.

## Audit order
1. Entry points, configuration, deployment, dependency versions.
2. Database backends, schema/migrations, concurrency and transaction safety.
3. Provider registry and exact retailer configuration.
4. Manual scans, autoscan, duplicate suppression, caching and price proof.
5. Discord component acknowledgement, locks, retries, persistent views and permissions.
6. Posting, website handoff, data contracts and failure recovery.
7. Tests and CI coverage.
8. Cleanup of verified dead, duplicate, conflicting and temporary code.

## Validation required before completion
- SQLite and Turso-compatible transaction tests, including rollback and reconnect failure cases.
- Provider configuration tests proving runtime settings reach the registered provider.
- One authoritative autoscan policy and implementation with public/private fallback tests.
- Full pytest execution in CI, compile/import checks, and deployment smoke validation.
- Bot-to-site contract tests and live acceptance.

## Backlog
None. This audit is the single active task.
