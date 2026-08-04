# Active Task

## Status
Active — canonical SniperPlug app `1779293887444` is online on Python 3.11.15. Duplicate app `1785806676351` is offline and must remain offline. PR #210 process-isolated native libSQL is deployed and production claims fell from 12–29 seconds to 0.63–1.44 seconds in the first verified window.

## Scope
Complete one consolidated runtime-stability pass without weakening exact Walmart item, seller, selected-offer, variant, condition, fulfillment, current-price, trusted original-price, duplicate, category, or per-server threshold proof.

## Confirmed production state
- Canonical app: `1779293887444` — online.
- Duplicate app: `1785806676351` — offline.
- Python: 3.11.15.
- Main bot PID and native libSQL worker PID are different.
- Startup confirms `native_libsql_in_gateway_process=false` and exact large-integer text transport.
- PR #208 separated catalog discovery from exact verification.
- PR #209 stopped scheduled rechecks from triggering aggressive 24/4 drain mode and repaired legacy hourly schedules.
- PR #210 moved the synchronous native libSQL driver into a dedicated child process.
- The first post-PR #210 sample contained no new event-loop, heartbeat, or gateway failure line.
- Exact claim timings in that sample were 0.63s, 1.29s, 0.79s, and 1.44s.

## Consolidated stability findings
- The previous whole-job Walmart provider lock can let a slow catalog or manual discovery job delay the exact worker for minutes even though the database no longer blocks Discord.
- Native libSQL recovery must never replay writes or retry a failed commit on a new connection because the original implicit transaction cannot follow that connection.
- SQL scripts require SQLite-aware statement splitting so triggers and quoted semicolons remain intact while errors still propagate.
- HP and Target standalone workers must use the same process-isolated, snowflake-safe database factory as the bot. The eBay watcher must use that factory before PR #200 can be considered ready.
- The bot must close the child database worker during graceful Discord shutdown.
- Production-critical dependency versions must be pinned to the versions proven by Python 3.11 CI.
- A simultaneous outage of all movie-ticket sources is degraded upstream health, not an application traceback every poll.

## Current branch
`fix/full-runtime-stability-pass`

This branch is intentionally isolated from production until all of the following pass:
- Complete Python 3.11 test suite.
- Complete Python 3.12 test suite and import smoke check.
- `pip check` on both CI paths.
- Child-process row and exact snowflake round trips.
- Worker death and operation-timeout recovery.
- Proof that slow child work does not starve the parent event loop.
- Proof that write/commit failures are not replayed across connections.
- Trigger and quoted-semicolon SQL script tests.
- Exact-priority Walmart request scheduling under catalog pressure.
- Automatic movie-source total-outage cache preservation without traceback spam.
- Static proof that the bot, HP watcher, and Target watcher share one production database factory.

## Deployment boundary
- Do not modify or deploy the duplicate app.
- Do not merge PR #200 during this stability pass.
- Do not lower alert thresholds or relax exact verification gates.
- Do not merge the stability branch until all CI checks pass and the final diff is reviewed as one unit.

## Follow-up after stability
Rebase PR #200 onto the stable main branch, replace its standalone database construction with the shared process-isolated factory, rerun its targeted and full suites, and keep live eBay deployment blocked on production eBay credentials and Buy API approval.
