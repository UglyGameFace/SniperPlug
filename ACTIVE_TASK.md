# Active Task

## Status
Active — Walmart alerts and queue draining are working live, but the existing Discloud app is still running Python 3.14.6 instead of the repository-pinned Python 3.11 runtime, and native Turso claim calls continue to stall Discord's event loop.

## Scope
Restore reliable Walmart background alerts without weakening exact item, seller, offer, current-price, original-price, duplicate, category, or per-server threshold proof.

## Production findings
- The 420 Lobby is correctly enrolled in live fanout with exact guild ID `1514374173517152418`.
- PR #202 fixed the original backpressure arithmetic and is deployed.
- Production logs `Global Walmart catalog discovery paused by exact-detail backpressure active` above the 450-row limit.
- Catalog route advancement is paused, so discovery no longer floods the queue.
- PR #203 is deployed and drain mode is active at 24 claims / concurrency 4.
- The due queue fell from 2,683 to 2,503 during the sampled live run.
- `new/retry due` is normally zero; the remaining queue is scheduled rechecks.
- Exact-deal fanout delivered a guild post and multiple DMs with zero public/DM errors.
- Claim, fetch, and store timings are now visible. Walmart fetches take roughly 10–15 seconds off-loop; store work is usually 1–4 seconds.
- The Turso claim stage is highly variable and reached 29.45 seconds during an event-loop-stall cluster.
- Event-loop lag still reaches roughly 13–17 seconds during exact queue work.
- The only captured traceback is the unrelated Atom/Fandango/Gofobo movie-ticket watcher failing safely.
- Runtime identity still reports Python 3.14.6 even though root `discloud.config` pins `VERSION=3.11`.
- Discloud commit updated application code but did not rebuild the existing application's language runtime.

## Completed changes
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

## Validation completed
- Python compilation passed.
- Import smoke check passed.
- Python Check passed.
- Full repository suite passed: 962 tests.
- Batched claim regression proves three queue operations regardless of rows claimed.
- Drain-mode regression proves bounded 24/4 escalation behavior.
- Recheck-cadence regressions cover 50%+, 30–49%, 10–29%, no-reference, under-threshold, and unavailable products.
- Health regression separates first-time/retry due work from scheduled rechecks.
- Terminal identity exclusion and exact-candidate off-loop tests remain passing.
- Live queue drain, backpressure, guild fanout, and DM fanout are confirmed.

## Remaining live definition of done
- Recreate or otherwise rebuild the Discloud application under Python 3.11 without losing environment secrets or the rollback path.
- Runtime identity reports Python 3.11.x.
- Drain mode and exact-deal fanout continue under the rebuilt runtime.
- Claim timings no longer produce sustained event-loop stalls or Discord websocket-behind warnings.
- Queue continues trending downward and catalog discovery resumes only below the safe pressure limit.

## Blockers
- Discloud's existing-app commit path updates code but has left the application on Python 3.14.6.
- A safe runtime migration requires preserving the current app as rollback and confirming the local or backed-up environment configuration before uploading a replacement app.

## Backlog
- Improve the separate movie-ticket source availability behavior after the Walmart alert task is fully closed.
- Review optional PyNaCl/davey voice dependencies separately; they do not affect Walmart alerts.
