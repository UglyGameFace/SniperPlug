# Active Task

## Status
Active — PR #221 is deployed and its original fanout cursor scan is production-verified as removed. The next dominant Turso blocker is the Walmart catalog queue enqueue path; implementation is in progress on `fix/walmart-catalog-queue-write-amplification`.

## Scope
Eliminate repeated Walmart catalog queue write amplification without dropping discovered item IDs, delaying new or changed candidates, weakening exact item/seller/offer/variant/shipping/reference proof, changing per-server thresholds, or breaking retry/recheck scheduling.

## Production evidence
- The former `SELECT queue.item_id, queue.verified_at, queue.snapshot_json` warning is absent after PR #221.
- The replacement fanout cursor-key read is fast: about 0.14-0.19 seconds remotely in the supplied log window.
- Catalog batches repeatedly issue two or three `INSERT INTO walmart_exact_detail_queue` operations for 75-100 deduplicated candidates.
- Those inserts took about 2.1-10.0 seconds remotely each; following commits took about 2.0-7.7 seconds.
- Unrelated reads and exact claims then waited behind the serialized database worker.
- Exact claim timing still spiked to 9.81s, 11.95s, and 18.80s while the actual fetch work remained much smaller.
- Catalog batches stretched to roughly 22-40 seconds during the worst write windows.

## Root cause
- Global catalog discovery runs every 60 seconds and calls `enqueue_walmart_exact_verification_candidates_bulk()`.
- That function re-runs raw table/index initialization on every enqueue instead of sharing the exact worker's once-per-connection schema cache.
- The enqueue batch is limited to 40 rows, so a normal 75-100 item pass still uses two or three remote UPSERT statements.
- `ON CONFLICT DO UPDATE` rewrites every rediscovered row even when its discovery projection is unchanged and recent.
- Each unnecessary update maintains multiple queue indexes and is followed by a separate remote commit on the one serialized Turso connection.
- `discovered_count` is not read by production behavior; it is only maintained by the enqueue path and tests.

## Implementation plan
- Reuse `ensure_exact_runtime_schema_once()` so catalog and exact workers share one schema initialization per live connection.
- Load one bounded primary-key projection for the current item IDs before writing.
- Persist only rows that are new, materially changed, stale enough to need a retention heartbeat, or require pending/retry/failed rearming.
- Skip the write and commit entirely for recent unchanged rediscoveries.
- Increase the conservative multi-row batch from 40 to 60 rows: 60 x 16 = 960 parameters, below SQLite's historical 999-variable ceiling.
- Add enqueue telemetry for persisted rows, unchanged rows, and write-statement count.
- Preserve immediate insertion of new IDs, meaningful discovery metadata updates, retry rearming, queue pressure reporting, retention cleanup, and exact-proof ownership.

## Definition of Done
- Targeted tests cover new-row batching, identical recent rediscovery no-op behavior, stale retention refresh, changed metadata/price persistence, retry rearming, source-label changes, and schema-cache wiring.
- Full Python 3.11 suite passes.
- Python 3.12 check, import smoke, `pip check`, and `compileall` pass.
- Final diff contains no temporary, duplicate, or partially superseded enqueue implementation.
- PR is reviewed, mergeable, and not behind `main`.
- After merge and canonical Discloud deployment, production logs show ordinary catalog passes no longer issuing repeated multi-second queue inserts/commits for unchanged rows and exact claim/store lock waits materially fall.

## Current branch
`fix/walmart-catalog-queue-write-amplification`

## Deployment boundary
- Canonical app: `1779293887444`.
- Duplicate app `1785806676351` must never be targeted.
- PR #200 remains isolated and unmerged.
- Do not claim production completion until live logs verify write suppression and reduced lock waits.

## Backlog
- Investigate remaining isolated multi-second `WITH picked AS` fanout/event claims only after catalog queue write amplification is resolved.
- Audit unrelated repeated `CREATE TABLE/INDEX IF NOT EXISTS` paths after this active task closes.
- Verify PR #220 seller/shipping card output against live Walmart payloads after database contention is resolved.
