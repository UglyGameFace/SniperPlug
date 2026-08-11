# Active Task

## Status
**Complete — SP-TURSO-STARTUP satisfied its Definition of Done in production after merged PR #230.** Canonical Discloud app `1779293887444` redeployed merged `main` at `23e6d0fd77f3fe35921bb6d0fffcc483c9cb8420` and completed a clean startup through the new readiness path.

## Task
**SP-TURSO-STARTUP — Keep SniperPlug alive through prolonged retryable Turso outages**

## Scope completed
1. Preserve the stable-read gate before schema/migration work.
2. Never run migrations while Turso's read path is degraded.
3. Never turn retryable Hrana 502/EOF/worker-transport failures into the old timed startup crash loop.
4. Keep non-transient database/configuration errors fail-fast.
5. Keep writes, transaction-adjacent failures, and unknown commit outcomes non-replayable.
6. Preserve the process-isolated Turso worker and canonical database identity.

## Root cause
- PR #229 correctly added a stable-read gate but still raised `TimeoutError` after 300 seconds of sustained retryable failures.
- Production hit that exact branch on Aug 10 while Turso repeatedly returned Hrana `502 Bad Gateway` / `upstream forward failed`.
- A parent worker timeout or lost worker/pipe during a read could also bypass the safe read retry path because the wrapper only caught `LibsqlWorkerError`.

## Changes delivered by PR #230
- The former 300-second readiness timeout is now a degraded-status reporting window, not a fatal deadline.
- Retryable startup failures continue waiting with bounded exponential backoff while migrations remain held.
- Prolonged outages emit `retryable_outage_fatal=false` telemetry instead of terminating startup.
- The readiness gate resets suspect workers between retryable outer probes.
- Safe read-only retry recognizes parent worker timeouts and known worker/pipe transport-loss errors.
- Writes still never replay after an uncertain failure.

## Validation
- Import smoke check: PASS.
- Python Check on Python 3.12: PASS.
- Python tests on Python 3.11.15: PASS.
- `pip check`: PASS.
- `python -m compileall -q sniperplug tests *.py`: PASS.
- Full Python 3.11 suite: 1,148 passed with one upstream `audioop` deprecation warning.
- Final branch inspection found no duplicate startup gate, alternate database, temporary fallback, or unrelated feature change.

## Live production proof — Aug 11, 2026
- Merged `main` deployed automatically to canonical Discloud app `1779293887444`.
- Startup logged `Turso startup readiness confirmed probes=3 consecutive_successes=3 elapsed_s=1.0 status_window_s=300.0 retryable_outage_fatal=false`.
- Runtime telemetry logged `retryable_startup_outage_fatal=false`.
- Database connected as `turso-process-isolated` and schema initialization completed.
- Discord gateway connected and `SniperPlug online as SniperPlug#1450` was logged.
- The old `TimeoutError: Turso startup readiness did not stabilize within 300.0s` appears only in the historical Aug 10 log section and did not recur after the Aug 11 deployment.

## Cleanup status
- Startup task is closed. No additional startup patch is required from the supplied production evidence.
- Two separate runtime findings from the same post-deploy log are intentionally not mixed into this task: repeated Discord gateway lag warnings (`websocket is 41.5s behind`) and the high Walmart seller-identity block rate.

## Backlog
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings during Walmart drain-mode exact verification.
- Investigate the high volume of Walmart `seller` identity blocks and transient exact-fetch failures.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into unrelated work.
