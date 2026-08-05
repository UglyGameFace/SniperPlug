# Active Task

## Status
Implementation complete and merge-ready on PR #221 (`fix/walmart-fanout-cursor-read`). Production remains unclaimed until merged `main` is deployed to canonical Discloud app `1779293887444` and verified in live logs.

## Scope
Eliminate the repeated slow read that scans verified Walmart queue snapshots for global public/DM fanout, without delaying verified deal delivery, skipping events, weakening exact-offer proof, or changing per-server thresholds.

## Production evidence
- The old `SELECT COUNT(*) FROM walmart_exact_detail_queue` warning no longer appears after PR #219.
- `SELECT queue.item_id, queue.verified_at, queue.snapshot_json` repeatedly took about 7–20 seconds remotely.
- Unrelated operations then waited behind the single process-isolated Turso connection for comparable durations.
- Exact verification still worked, but affected cycles reported claim/store times as high as 16.50s/21.54s.
- The slow query ran after the 20-second exact worker and after the 60-second catalog worker, including empty fanout polls.

## Root cause
- `_ingest_verified_queue_events_bulk()` selected large `snapshot_json` values while locating the next cursor page.
- Its joined `OR` watermark predicate did not match the queue's existing indexes.
- The existing verified index was `(verified_at, exact_discount_bps DESC)`; fanout filters by `status = 'verified_markdown'` and orders by `(verified_at, item_id)`.
- The process-isolated driver fully consumes and serializes every query result inside the worker, so an inefficient scan/large-row read blocked every other database operation.

## Implemented changes
- Added a fanout-specific partial index on `(verified_at, item_id)` for nonempty exact `verified_markdown` snapshots.
- Load the one-row fanout watermark separately.
- Replaced the joined `OR` predicate with indexed row-value pagination: `(verified_at, item_id) > (?, ?)`.
- Ordinary/empty polls select only lightweight cursor keys and never retrieve `snapshot_json`.
- Snapshot JSON is fetched only for the bounded selected keys through a VALUES join.
- Snapshot loading revalidates the exact `(item_id, verified_at)` version and preserves selected order.
- The watermark advances to the final selected key even if a selected row changes between phases; a newer version remains eligible on the next pass.
- Existing durable event insertion, leases, duplicate suppression, public thresholds, DM matching, and exact item/seller/offer/variant/shipping/reference gates remain unchanged.
- Schema/index initialization remains cached once per live database connection.

## Validation
- Python 3.11 full suite: **1,107 passed**, one upstream `audioop` deprecation warning.
- Python 3.12 Python Check: passed.
- Import smoke check: passed.
- `pip check`: passed.
- `compileall` across app, tests, and entry points: passed.
- Query-plan regression requires the partial cursor index and proves no temporary ordering B-tree is used.
- Functional regressions cover empty polls, equal-timestamp ordering, bounded pagination, snapshot version changes, deterministic watermark advancement, event insertion, and one-time schema/index initialization.
- Static regression prevents the original joined snapshot cursor query from returning.
- Qodo's high-level assessment recommends the current partial-index/two-phase approach; no correctness review threads remain.

## Cleanup and conflict inspection
- Final diff contains only this task record, the fanout implementation, the upgraded existing schema-cache test, and focused cursor regressions.
- No temporary or duplicate implementation files remain.
- PR #221 is mergeable against current `main` and is not behind its base.
- No threshold, identity, shipping, reference, duplicate, event lease, or DM preference behavior was weakened.
- PR #200 remains isolated and unmerged.

## Current branch
`fix/walmart-fanout-cursor-read`

## Deployment boundary
- Canonical app: `1779293887444`.
- Duplicate app `1785806676351` must never be targeted.
- Do not merge PR #200 during this task.
- Production completion requires a post-deploy log window proving the former `SELECT queue.item_id, queue.verified_at, queue.snapshot_json` multi-second query and its lock-wait cascade are gone.

## Backlog
- Audit remaining repeated schema `CREATE TABLE/INDEX IF NOT EXISTS` calls after this task closes. Most observed delays were downstream lock waits behind fanout, but any remaining redundant DDL should be addressed separately.
- Verify PR #220 seller/shipping card output against live Walmart payloads after database contention is resolved.
