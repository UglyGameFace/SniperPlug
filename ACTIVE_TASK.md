# Active Task

## Status
Implementation complete and merge-ready on PR #222 (`fix/walmart-catalog-queue-write-amplification`). Production remains unclaimed until merged `main` is deployed to canonical Discloud app `1779293887444` and verified in live logs.

## Scope
Eliminate repeated Walmart catalog queue write amplification without dropping discovered item IDs, delaying new or changed candidates, weakening exact item/seller/offer/variant/shipping/reference proof, changing per-server thresholds, or breaking retry/recheck scheduling.

## Production evidence
- The former `SELECT queue.item_id, queue.verified_at, queue.snapshot_json` warning is absent after PR #221.
- The replacement fanout cursor-key read is fast: about 0.14-0.19 seconds remotely in the supplied log window.
- Catalog batches repeatedly issued two or three `INSERT INTO walmart_exact_detail_queue` operations for 75-100 deduplicated candidates.
- Those inserts took about 2.1-10.0 seconds remotely each; following commits took about 2.0-7.7 seconds.
- Unrelated reads and exact claims then waited behind the serialized database worker.
- Exact claim timing spiked to 9.81s, 11.95s, and 18.80s while the actual fetch work remained much smaller.
- Catalog batches stretched to roughly 22-40 seconds during the worst write windows.

## Root cause
- Global catalog discovery runs every 60 seconds and calls `enqueue_walmart_exact_verification_candidates_bulk()`.
- That function re-ran raw table/index initialization on every enqueue instead of sharing the exact worker's once-per-connection schema cache.
- The enqueue batch was limited to 40 rows, so a normal 75-100 item pass still used two or three remote UPSERT statements.
- `ON CONFLICT DO UPDATE` rewrote every rediscovered row even when its discovery projection was identical and recent.
- Each unnecessary update maintained multiple queue indexes and was followed by a separate remote commit on the one serialized Turso connection.
- Rotating catalog route labels could also change for the same product even though `route_hint` is discovery telemetry, not exact proof.
- `discovered_count` is not read by production behavior; it is only maintained by the enqueue path and tests.

## Implemented changes
- Catalog enqueue now reuses `ensure_exact_runtime_schema_once()` so catalog and exact workers share one schema initialization per live connection.
- One bounded primary-key projection loads current discovery state for the candidate IDs before writing.
- Rows persist only when they are new, materially changed, stale enough for a six-hour retention heartbeat, or require pending/retry/failed rearming.
- Recent identical rediscoveries skip both queue UPSERTs and the enqueue commit.
- Rotating nonempty route labels do not force a rewrite by themselves; an empty stored route is still filled, and a route-derived priority increase still persists.
- The conservative multi-row batch increased from 40 to 60 rows: 60 x 16 = 960 parameters, below SQLite's historical 999-variable ceiling.
- Queue summaries now expose persisted rows, unchanged rows, and write-statement count.
- The UPSERT still leaves exact status, verified timestamps/prices, snapshots, leases, attempts, seller/offer/variant/condition/fulfillment proof, and recheck tiers under the exact worker's ownership.

## Validation
- Python 3.11 full suite: **1,113 passed**, one upstream `audioop` deprecation warning.
- Python 3.12 Python Check: passed.
- Import smoke check: passed.
- `pip check`: passed.
- `compileall` across app, tests, and entry points: passed.
- Focused regressions prove 75 new rows use two writes; recent identical rediscovery uses zero writes and zero commits; stale rows receive retention heartbeats; retry rows rearm; changed price/title/source persists; rotating route-only changes are suppressed; and schema/cache/parameter boundaries remain valid.
- Qodo recommends the bounded pre-read plus selective UPSERT approach for the serialized Turso worker.
- No inline correctness review threads remain.

## Cleanup and conflict inspection
- Final diff contains only this task record, the enqueue-path implementation, and focused regressions.
- No temporary, duplicate, compatibility-shim, or partially superseded enqueue implementation remains.
- New IDs, meaningful metadata/price changes, retry rearming, source changes, and retention heartbeats remain immediate.
- Exact proof, thresholds, duplicate suppression, event leases, public fanout, and DM behavior are unchanged.
- PR #222 is mergeable against current `main` and is not behind its base.
- PR #200 remains isolated and unmerged.

## Current branch
`fix/walmart-catalog-queue-write-amplification`

## Deployment boundary
- Canonical app: `1779293887444`.
- Duplicate app `1785806676351` must never be targeted.
- Production completion requires post-deploy logs showing ordinary repeated catalog passes report mostly unchanged rows with zero or few write statements, repeated multi-second queue inserts/commits materially fall, and exact claim/store lock waits improve.

## Backlog
- Investigate remaining isolated multi-second `WITH picked AS` fanout/event claims only after catalog queue write amplification is production-verified.
- Audit unrelated repeated `CREATE TABLE/INDEX IF NOT EXISTS` paths after this active task closes.
- Verify PR #220 seller/shipping card output against live Walmart payloads after database contention is resolved.
