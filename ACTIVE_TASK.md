# Active Task

## Status
Active — Walmart discovery backpressure is live, but exact rechecks are still maturing faster than the six-row worker drains them and event-loop lag remains during queue work.

## Scope
Restore reliable Walmart background alerts without weakening exact item, seller, offer, current-price, original-price, duplicate, category, or per-server threshold proof.

## Production findings
- The 420 Lobby is correctly enrolled in live fanout with exact guild ID `1514374173517152418`.
- PR #202 fixed the original backpressure arithmetic and is deployed.
- Production now logs `Global Walmart catalog discovery paused by exact-detail backpressure active` above the 450-row limit.
- Catalog route advancement stopped as intended, so discovery no longer floods the queue.
- The queue remains near 2,800 due rows while successful six-item batches run.
- Queue health currently mixes first-time/retry work with scheduled verified-product rechecks.
- Global verification classifies products at the 10% discovery floor; every `verified_markdown` was scheduled again after one hour, including 10–49% sales that cannot meet the server's 50% public threshold.
- The old row claim path used one ordered SELECT plus an UPDATE and verification SELECT for every claimed row, multiplying Turso operations.
- Production still reports 2–15 second event-loop stalls during exact queue work, although search and exact payload parsing are already off-loop.
- The unrelated Atom/Fandango/Gofobo traceback was classified as a safely handled movie-ticket source outage.

## Current changes
- Add a bounded drain mode: 24 claims with concurrency 4 only while actionable due is at least 450; normal mode remains 6/2.
- Replace per-row queue leasing with one ordered SELECT, one guarded batch UPDATE, and one verification SELECT.
- Preserve pending/retry priority ahead of scheduled rechecks.
- Exclude terminal identity failures from claims.
- Keep 50%+ verified markdowns on a one-hour recheck.
- Recheck 30–49% markdowns every six hours and 10–29% markdowns every twelve hours.
- Recheck no-reference products every twelve hours and under-threshold/unavailable products every twenty-four hours.
- Split health output into `new/retry due` and `scheduled rechecks due` while preserving total actionable backpressure.
- Add claim, fetch, and store timing to each queue batch log.

## Validation required
- Python compilation.
- Import smoke check.
- Full repository pytest workflow.
- Targeted queue claim, lease, health-split, drain-mode, exact-proof, and recheck-cadence tests.
- Review all changed files for temporary or duplicate implementations.
- Confirm the branch is current with `main`, mergeable, and has no unresolved review threads.

## Live definition of done
- Deploy the merged update to Discloud app `1779293887444`.
- Backpressure remains active while total due exceeds 450.
- Queue logs show `mode drain` and `batch/concurrency 24/4`.
- `new/retry due` reaches zero or remains near zero while scheduled rechecks trend downward.
- Catalog discovery resumes only after pressure falls below the safe limit.
- Exact-deal fanout continues during the drain.
- No sustained Discord websocket-behind warning occurs.
- Batch timing identifies and removes any remaining multi-second blocking stage.

## Blockers
Live validation cannot be completed until the next patch passes CI, is merged, and is redeployed.

## Backlog
- Improve the separate movie-ticket source availability behavior after the Walmart alert task is fully closed.
- Review optional PyNaCl/davey voice dependencies separately; they do not affect Walmart alerts.
