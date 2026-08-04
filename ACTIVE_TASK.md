# Active Task

## Status
Active — the Walmart queue-drain patch is code-complete and validated; live Discloud deployment and runtime proof remain.

## Scope
Restore reliable Walmart background alerts without weakening exact item, seller, offer, current-price, original-price, duplicate, category, or per-server threshold proof.

## Production findings
- The 420 Lobby is correctly enrolled in live fanout with exact guild ID `1514374173517152418`.
- PR #202 fixed the original backpressure arithmetic and is deployed.
- Production now logs `Global Walmart catalog discovery paused by exact-detail backpressure active` above the 450-row limit.
- Catalog route advancement stopped as intended, so discovery no longer floods the queue.
- The queue remained near 2,800 due rows while successful six-item batches ran.
- The due total mixed first-time/retry work with scheduled verified-product rechecks.
- Global verification classifies products at the 10% discovery floor; every `verified_markdown` was scheduled again after one hour, including 10–49% sales that cannot meet the server's 50% public threshold.
- The old row claim path used one ordered SELECT plus an UPDATE and verification SELECT for every claimed row, multiplying Turso operations.
- Production still reported 2–15 second event-loop stalls during exact queue work, although search and exact payload parsing were already off-loop.
- The unrelated Atom/Fandango/Gofobo traceback was classified as a safely handled movie-ticket source outage.

## Changes
- Added bounded drain mode: 24 claims with concurrency 4 only while actionable due is at least 450; normal mode remains 6/2.
- Replaced per-row queue leasing with one ordered SELECT, one guarded batch UPDATE, and one verification SELECT.
- Preserved pending/retry priority ahead of scheduled rechecks.
- Kept terminal identity failures excluded and fail-closed.
- Kept 50%+ verified markdowns on a one-hour recheck.
- Changed 30–49% markdown rechecks to six hours and 10–29% markdown rechecks to twelve hours.
- Changed no-reference rechecks to twelve hours and under-threshold/unavailable rechecks to twenty-four hours.
- Split health output into `new/retry due` and `scheduled rechecks due` while preserving total actionable backpressure.
- Added claim, fetch, and store timing to each queue batch log.
- Added production-shaped regression coverage for batch leasing, terminal exclusion, health splitting, drain mode, exact proof, and tiered rechecks.

## Validation
- Python compilation passed.
- Import smoke check passed.
- Python Check passed.
- Full repository suite passed: 962 tests.
- Batched claim regression proves three queue operations regardless of rows claimed.
- Drain-mode regression proves bounded 24/4 escalation behavior.
- Recheck-cadence regressions cover 50%+, 30–49%, 10–29%, no-reference, under-threshold, and unavailable products.
- Health regression separates first-time/retry due work from scheduled rechecks.
- Terminal identity exclusion and exact-candidate off-loop tests remain passing.
- Changed-file inspection found no temporary, backup, or duplicate implementation files.
- Branch is current with `main`, mergeable, and has no unresolved review threads.

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
Live validation cannot be completed until the validated patch is merged and redeployed.

## Backlog
- Improve the separate movie-ticket source availability behavior after the Walmart alert task is fully closed.
- Review optional PyNaCl/davey voice dependencies separately; they do not affect Walmart alerts.
