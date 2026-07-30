# Active Task

## Task
Perform a complete execution-path audit of the SniperPlug Discord bot and its integration with `UglyGameFace/SniperPlug-Site`. Repair confirmed discrepancies only after inspecting real callers, tests, guards, compatibility layers, storage, configuration, and deployment behavior.

## Status
Audit active. No completion claim until implementation, targeted tests, regression checks, compilation/static validation, cleanup, conflict inspection, and bot/site integration validation pass.

## Confirmed findings
- Bot startup registers `CachedWalmartProvider(self.db, WalmartProvider(configured=False))` even though Settings exposes Walmart credentials and `WALMART_PROVIDER_ENABLED`; runtime wiring and configuration disagree.
- Multiple scanner/auto-scan implementations exist while startup registers only `native_auto_scan_runner`; references and obsolete paths require inspection before deletion or integration.
- CI compiles and smoke-imports but does not run the complete pytest suite.
- Storage maintenance blocks `setup_hook` before ready.
- Setup self-heal sets its one-shot flag before success, preventing retry after a failure.
- Broad exception handling appears across runtime services and must be classified into intentional isolation versus swallowed failures.

## Audit order
1. Entry points, configuration, deployment, dependency versions.
2. Database backends, schema/migrations, concurrency and transaction safety.
3. Provider registry and exact retailer configuration.
4. Manual scans, autoscan, duplicate suppression, caching and price proof.
5. Discord component acknowledgement, locks, retries, persistent views and permissions.
6. Posting, website handoff, data contracts and failure recovery.
7. Tests and CI coverage.
8. Cleanup of verified dead, duplicate, conflicting and temporary code.

## Backlog
None. This audit is the single active task.