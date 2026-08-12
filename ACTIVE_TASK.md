# Active Task

## Status
**Active — SP-WM-PAGE-RETRY-FANOUT. Live production logs after PR #234 confirm seller/offer identity blocking dropped to zero, but Walmart exact verification is now suffering repeated transient-retry storms that pause catalog discovery. The same logs also show a 67.3% verified Walmart event reaching DM delivery while the 40% public destination reports `not_alertable`, whose current telemetry does not identify the actual gate. No production-complete claim is allowed until both paths are traced, corrected where needed, and validated.**

## Task
**SP-WM-PAGE-RETRY-FANOUT — Stop Walmart page-recovery transient storms from starving fresh discovery, expose exact retry/public skip reasons, and preserve strict item/price/seller/offer/shipping/variant/reference safety.**

## Scope
1. Trace the real exact-worker execution path from queue claim through `CachedWalmartProvider` → `WalmartPageRecoveryProvider` → official API/page fallback → `_fetch_exact_candidate_off_loop()` → `_store_failure()` → fresh-work backpressure.
2. Determine why live batches repeatedly show 6–8 transient failures out of 8 claims even though seller identity blocks are now zero. Distinguish official API failures, product-page HTTP/challenge/network failures, price-change retries, outer exact-worker timeouts, and candidate-build failures.
3. Prevent page-recovery retry traffic from being indistinguishable from genuinely fresh first-pass exact work when applying the catalog pause policy. Preserve bounded retries and fail closed; do not bypass Walmart challenges, CAPTCHAs, or rate limits.
4. Align exact-worker/provider timeout budgets so the outer worker does not race or prematurely cancel a legitimate official API/page recovery operation while a background thread continues running.
5. Add bounded retry telemetry that exposes useful failure classes/reasons in live batch diagnostics without logging secrets or raw page bodies.
6. Trace the verified-event public fanout path for the live 67.3% KC Masterpiece event: DM matched/sent, while the 40% public destination returned `not_alertable`. Determine whether that was category suppression, public-quality proof, `should_alert`, or another intended gate. Add exact public skip-reason telemetry; correct only an actual logic defect and do not weaken category/threshold/dedupe/proof rules.
7. Fix the page-recovery one-time rearm count so unsupported DB cursor `rowcount` falls back to `SELECT changes()`; startup `recent_rows_rearmed=0` must be trustworthy.
8. Add focused regressions for the production failure shapes, then run full tests, dependency/static/compile validation, cleanup, diff/conflict inspection, PR review/CI inspection, merge, and post-merge canonical validation.

## Findings so far
- PR #234 is live and its primary seller-identity objective is confirmed: post-deploy exact batches consistently report `identity unavailable / safely blocked 0`, versus the previous 16–24 seller blocks per 24-item batch.
- Live exact batches now frequently show 6–8 `transient failures` out of 8 claims. These retries can raise fresh/retry pressure above the catalog pause threshold (`14/12`, later `38/12`) and stop discovery.
- `_fetch_exact_candidate_off_loop()` wraps the entire provider detail call in `asyncio.wait_for(..., timeout=8s)` and maps every provider exception to generic status `retry` with only `last_error` retaining the cause.
- The official Walmart provider itself permits a 12-second HTTP timeout. Product-page recovery separately defaults to an 8-second page HTTP timeout. The outer 8-second worker budget is therefore structurally shorter than the provider operations it encloses.
- The live batch wall-clock pattern is usually much shorter than four sequential 8-second timeouts at concurrency two, so outer timeout collision alone is not proven as the dominant production cause; fast HTTP/challenge/provider failures remain likely and need reason telemetry before changing retry policy blindly.
- `_store_failure()` sends generic retries through 5-minute exponential backoff (capped at 6 hours). `walmart_exact_queue_pressure` counts due `retry` rows as fresh work, and `FRESH_DISCOVERY_PAUSE_THRESHOLD` is 12. This directly explains why repeated page/provider retries can pause catalog intake.
- Product-page recovery intentionally treats HTTP 403/408/425/429/5xx, network/timeouts, and challenge pages as transient. That fail-closed boundary must remain.
- The page-recovery migration count currently uses `_safe_rowcount()`, which converts unsupported/negative cursor rowcount to zero instead of using the `SELECT changes()` fallback already used elsewhere in the queue runtime.
- Live fanout at 14:46 proves DM delivery works end to end for at least one strong verified Walmart event: KC Masterpiece at `$3.98`, 67.3% discount, `DM checked/matched/sent 1/1/1`.
- The same event did not publicly post to guild `1514374173517152418` despite a 40% threshold; the destination reported `not_alertable=1`. The public posting layer currently collapses category suppression, public-quality rejection, and `should_alert` rejection into the same counter, so the log alone cannot prove this was a bug.

## Safety invariants
- Exact requested Walmart item must match the exact detail/page item.
- Official selected-item price remains authoritative; page seller/offer recovery requires cent-for-cent price alignment.
- Seller, offer, variant, condition, fulfillment, and mandatory shipping must remain bound to the same selected offer.
- Delivered-price math must continue including mandatory marketplace shipping.
- Trusted reference-price proof, availability, threshold, category, duplicate, and public quality gates remain fail closed.
- No CAPTCHA/challenge bypass, proxy rotation, raw-page persistence, alternate-retailer proof substitution, or unrestricted historical queue rearm.

## Validation
- Implementation patch applied on `fix/walmart-page-retry-fanout`; targeted and full validation pending canonical CI.

## Cleanup / conflict inspection
- Runtime retry persistence is being centralized through one failure-storage policy so bulk and compatibility paths cannot drift. Final diff/conflict inspection pending.

## Blockers
- None currently. Live logs provide the production failure signature, but exact transient sub-reasons are not yet surfaced by current telemetry.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` after this task closes.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings after this active Walmart delivery task closes unless the current root cause directly explains them.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs after this task closes.
- PR #200 remains isolated and must not be mixed into this task.
