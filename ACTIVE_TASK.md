# Active Task

## Status
Active — production logs after PRs #218–#220 show the former full queue `COUNT(*)` is gone, but the global Walmart fanout cursor query is now serializing the remote Turso worker for 7–20 seconds per poll. Work is isolated on `fix/walmart-fanout-cursor-read`; no new deployment has occurred.

## Scope
Eliminate the repeated slow read that scans verified Walmart queue snapshots for global public/DM fanout, without delaying verified deal delivery, skipping events, weakening exact-offer proof, or changing per-server thresholds.

## Production evidence
- The old `SELECT COUNT(*) FROM walmart_exact_detail_queue` warning no longer appears.
- `SELECT queue.item_id, queue.verified_at, queue.snapshot_json` repeatedly takes about 7–20 seconds remotely.
- Unrelated operations then wait behind the single process-isolated Turso connection for comparable durations.
- Exact verification still works, but affected cycles report claim/store times as high as 16.50s/21.54s.
- The slow query runs after the 20-second exact worker and after the 60-second catalog worker, including empty fanout polls.

## Root cause findings
- `_ingest_verified_queue_events_bulk()` selects large `snapshot_json` values while locating the next cursor page.
- Its joined `OR` watermark predicate does not match the queue's existing indexes.
- The existing verified index is `(verified_at, exact_discount_bps DESC)`; fanout filters by `status = 'verified_markdown'` and orders by `(verified_at, item_id)`.
- The process-isolated driver fully consumes and serializes every query result inside the worker, so an inefficient scan/large-row read blocks every other database operation.

## Intended implementation
- Add a fanout-specific partial cursor index matching verified-markdown filtering and `(verified_at, item_id)` ordering.
- Load the single watermark row separately.
- Use a row-value cursor comparison rather than a joined `OR` predicate.
- Select only lightweight cursor keys first; fetch `snapshot_json` only for the bounded selected keys.
- Preserve deterministic cursor advancement even if a row changes between the key and snapshot reads.
- Keep durable event insertion, leases, duplicate suppression, public thresholds, DM matching, and exact verification unchanged.

## Definition of done
- Query-plan regression proves the cursor-key read uses the fanout index and does not scan snapshot payloads.
- Functional tests cover empty polls, multiple rows sharing one timestamp, bounded ordering, changed/missing rows between phases, event insertion, and watermark advancement.
- Existing global fanout, queue, catalog, exact-worker, public-post, and DM tests pass.
- Full Python tests, Python Check, import smoke, dependency check, and compilation pass.
- Review/conflict inspection finds no unresolved correctness or compatibility issue.
- Temporary, redundant, or conflicting code is removed before merge.
- Production is not claimed until merged `main` is deployed to canonical Discloud app `1779293887444` and live logs show the old snapshot query no longer causing multi-second remote/lock waits.

## Current branch
`fix/walmart-fanout-cursor-read`

## Deployment boundary
- Canonical app: `1779293887444`.
- Duplicate app `1785806676351` is being deleted and must never be targeted.
- Do not merge PR #200 during this task.
- Do not lower alert thresholds or weaken exact item/seller/offer/variant/shipping/reference proof.

## Backlog
- Audit repeated schema `CREATE TABLE/INDEX IF NOT EXISTS` calls only after this active fanout query task is complete; most current delays are downstream lock waits, but any remaining redundant DDL should be addressed separately.
- Verify PR #220 seller/shipping card output against live Walmart payloads after database contention is resolved.
