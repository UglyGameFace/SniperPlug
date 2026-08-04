# Active Task

## Status
Active — the Walmart alert recovery code is deployed and the production bot has been cut over successfully to a fresh Python 3.11.15 Discloud application. Final live validation of queue drain rate, Turso claim timing, Discord event-loop lag, and fanout continuity remains.

## Scope
Restore reliable Walmart background alerts without weakening exact item, seller, offer, current-price, original-price, duplicate, category, or per-server threshold proof.

## Production findings
- The 420 Lobby is correctly enrolled in live fanout with exact guild ID `1514374173517152418`.
- PR #202 fixed the original backpressure arithmetic and is deployed.
- Production logs `Global Walmart catalog discovery paused by exact-detail backpressure active` above the 450-row limit.
- Catalog route advancement is paused, so discovery no longer floods the queue.
- PR #203 is deployed and drain mode is active at 24 claims / concurrency 4.
- The due queue fell from 2,683 to 2,503 during the sampled Python 3.14 run.
- `new/retry due` was normally zero; the remaining queue was scheduled rechecks.
- Exact-deal fanout delivered a guild post and multiple DMs with zero public/DM errors.
- Claim, fetch, and store timings are visible. Walmart fetches took roughly 10–15 seconds off-loop; store work was usually 1–4 seconds.
- On the old Python 3.14.6 runtime, the Turso claim stage was highly variable and reached 29.45 seconds during an event-loop-stall cluster.
- Event-loop lag on the old runtime reached roughly 13–17 seconds during exact queue work.
- The only captured traceback was the unrelated Atom/Fandango/Gofobo movie-ticket watcher failing safely.
- Existing-app commits updated application code but did not rebuild the old application's Python runtime.
- A fresh replacement application was created and first failed because a normal CLI upload omitted the hidden root `.env`.
- The original application's Discloud backup contained the production `.env`; it was recovered locally without exposing values.
- A clean staging directory was built from tracked files plus an explicit root `.env`.
- The Discloud CLI preflight archive was verified to contain root `main.py`, `discloud.config`, `requirements.txt`, and `.env`, with the archived `.env` matching the recovered production file.
- The staged replacement commit succeeded using explicit globs `"**"` and `".env"`.
- Replacement app `1785806676351` is live on Python 3.11.15, connected to Turso, logged into Discord, and reports the correct eligible fanout guilds.
- Old app `1779293887444` remains stopped as rollback.

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
- Rebuilt production under the repository-pinned Python 3.11 runtime while preserving a stopped rollback application.

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
- Live queue drain, backpressure, guild fanout, and DM fanout were confirmed on the old runtime.
- Replacement runtime identity is confirmed as Python 3.11.15.
- Replacement Turso connection is confirmed.
- Replacement Discord login is confirmed.
- Replacement fanout enrollment includes guild `1514374173517152418`.
- Final app state is replacement online and rollback offline.

## Remaining live definition of done
- Observe multiple exact-verification cycles on Python 3.11 showing `mode drain` and `batch/concurrency 24/4` while due exceeds 450.
- Confirm `new/retry due` remains near zero and scheduled rechecks continue trending downward.
- Confirm claim timings no longer produce sustained event-loop stalls or Discord websocket-behind warnings.
- Confirm exact-deal fanout continues with zero public/DM errors.
- Confirm catalog discovery remains paused above 450 and resumes only below the safe pressure limit.
- Keep the old app stopped until the Python 3.11 runtime has enough production evidence for rollback retirement.

## Blockers
- No deployment blocker remains.
- Final closure depends on post-cutover production evidence from the Python 3.11 workload.

## Backlog
- Improve the separate movie-ticket source availability behavior after the Walmart alert task is fully closed.
- Review optional PyNaCl/davey voice dependencies separately; they do not affect Walmart alerts.
- Delete the stopped Python 3.14 rollback app and obsolete deployment branches only after final runtime validation.
