# Active Task

## Status
Implementation, targeted regression coverage, repository CI, compilation/static validation, and initial cleanup are complete on PR #230 (`hotfix/turso-startup-outage-hold-v2`). Production completion remains unclaimed until the PR is merged, canonical Discloud app `1779293887444` is redeployed, and live startup logs prove a prolonged retryable Turso outage no longer terminates the process.

## Task
**SP-TURSO-STARTUP — Keep SniperPlug alive through prolonged retryable Turso outages**

## Scope
1. Fix the startup failure shown immediately after merged PR #229 was deployed.
2. Preserve the stable-read gate before schema/migration work.
3. Never run migrations while Turso's read path is degraded.
4. Never turn retryable Hrana 502/EOF/worker-transport failures into a timed startup crash loop.
5. Keep non-transient database/configuration errors fail-fast.
6. Keep writes, transaction-adjacent failures, and unknown commit outcomes non-replayable.
7. Preserve the process-isolated Turso worker and canonical database identity.
8. Validate focused startup/retry safety plus the repository CI suite before merge.

## Findings
- PR #229 correctly added a stable-read gate, but its implementation still raised `TimeoutError` after 300 seconds of sustained retryable failures.
- The supplied production log hit that exact branch after twelve readiness probes: Turso repeatedly returned Hrana `502 Bad Gateway` with `upstream forward failed`, then `_await_turso_startup_readiness()` raised and `setup_hook` terminated the bot.
- This contradicted PR #229's stated goal of keeping the startup process alive and backing off instead of entering a crash loop.
- `libsql.connect()` creating a connection object is not proof that the selected Turso host/route can execute reads; the remote database path can remain transiently unavailable after the worker itself initializes.
- The prior retry wrapper only caught `LibsqlWorkerError`; a parent worker timeout or lost worker/pipe during a read could therefore bypass the safe read retry path even though replaying a read-only `SELECT` is safe.

## Changes
- The former 300-second readiness timeout is now a degraded-status reporting window, not a fatal deadline.
- Retryable startup failures continue waiting indefinitely with bounded exponential backoff while migrations remain held.
- A prolonged outage emits an explicit status log stating `retryable_outage_fatal=false` instead of raising `TimeoutError`.
- The readiness gate resets the suspect worker after an exhausted retryable read before the next outer probe.
- Safe parent read retry now also recognizes parent worker timeouts and known worker/pipe transport-loss errors.
- Runtime read retries may recover those parent worker transport failures, while writes still never replay.
- Startup telemetry now reports `startup_readiness_status_window_s` and `retryable_startup_outage_fatal=false` rather than implying the window is a fatal timeout.

## Validation
- Focused regressions cover parent-worker read timeout recovery and prove a parent-worker timeout on a write is never replayed.
- A production-shaped Hrana 502 regression exceeds the readiness reporting window and then recovers; startup completes rather than raising the old timeout.
- Existing consecutive-success, success-streak reset, non-transient failure, bounded read retry, EOF read recovery, write no-replay, and transaction-adjacent no-replay tests remain passing.
- GitHub Import smoke check: **PASS**.
- GitHub Python Check (Python 3.12; dependency check, compile, import smoke, full suite): **PASS**.
- GitHub Python tests (Python 3.11.15): **PASS**.
- `pip check`: **PASS — no broken requirements**.
- `python -m compileall -q sniperplug tests *.py`: **PASS**.
- Full Python 3.11 suite: **1,148 passed, 1 upstream `audioop` deprecation warning**.

## Cleanup and conflict inspection
- The fix modifies the existing readiness/retry implementation; no second startup gate, fallback database, compatibility shim, or duplicate retry service was added.
- The production database remains the source of truth; there is no unsafe local SQLite failover or split-brain write path.
- Embedded replicas remain opt-in and unchanged.
- Branch was created directly from merged `main` at `a99ebcbb651aa01083a880ac7714adb53bd07124` (PR #229) and was zero commits behind at task start.
- Final branch-vs-main comparison, changed-file inspection, PR mergeability, and review-thread inspection are required once this status commit finishes CI.

## Current branch / PR
- Branch: `hotfix/turso-startup-outage-hold-v2`
- PR: #230 — `Keep startup alive through prolonged Turso outages`

## Deployment boundary
- Canonical Discloud app: `1779293887444`.
- Duplicate app `1785806676351` must never be targeted.
- Do not claim production completion until merged `main` is deployed and live logs show either stable Turso readiness or continued bounded waiting during a retryable outage without an unhandled startup exception.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` only after this Turso startup task satisfies its Definition of Done.
- Investigate the high volume of Walmart `seller` identity blocks and transient exact-fetch failures as a separate exact-verification task.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into this task.
