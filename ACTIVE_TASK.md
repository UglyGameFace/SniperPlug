# Active Task

## Status
**SP-WM-DEAL-DELIVERY-STALL — COMPLETE in production.**

The Walmart recovery task has passed code validation and live production acceptance. Canonical `main` includes PR #282 merge `9dcdf197f225e76dcf8adad546d49ea6fe3412e6`.

## Production acceptance
A fresh post-#282 deployment proved the intended behavior:

- mature confirmed dual-no-price retries remain visible at the bounded diagnostic cap (`>=600`) and page recovery remains bounded (`>=64`);
- those mature no-price rows no longer trigger the split-aware 600-row catalog emergency stop by themselves;
- catalog discovery resumed and advanced continuously from routes `143-144` through `163-164` during the acceptance window;
- genuine fresh/ordinary pressure still pauses discovery at the existing 12-row safety threshold, with observed pauses at `33/12`, `27/12`, `26/12`, and `21/12`;
- drain mode correctly switched to `24/4` under fresh pressure and returned to normal `8/2` behavior as pressure drained;
- catalog batches continued producing safe fresh proof;
- exact verification continued producing verified rows, official was prices, markdown outcomes, no-reference outcomes, and unavailable outcomes without weakening proof gates;
- at 19:37 production produced `verified=3`, `fresh catalog proof=1/3`, `official was prices=2`, and `markdowns=2`; durable fanout then processed the exact event with one public post and one matched/sent DM while another guild was safely skipped by the existing `quality:review_or_watchlist` gate;
- the old `664/600 exact-detail emergency backpressure` freeze did not recur when genuine fresh/ordinary pressure was below 12;
- page circuit remained closed and no page-concurrency regression was observed.

## Validated ancestry
- PR #282 validated head: `0206b9ea26291ec5bdd18427e03f432759d6b6f9`
- PR #282 exact-head validation: full suite **1,425 passed**, 1 warning; targeted Walmart regressions, native libsql, compile/import/pip/diff/scope/conflict/clean-head checks passed
- PR #282 merge / production-accepted main: `9dcdf197f225e76dcf8adad546d49ea6fe3412e6`
- PR #281 merge: `efa9c667e7e96ceaa3553e3e909611b4a6cb024c`
- PR #280 merge: `ab0b8b8cc1c66724420843cebcca4e414137a856`
- PR #279 merge: `54f1a84b42b6c0f0cec7f8182483994efb918efd`
- PR #278 merge: `15919739996093a1cb55361ee5539e96a7aebb3f`
- PR #277 merge: `a449bd43ea319f73c63c4604e16a4220225b7ec5`
- PR #276 merge: `62889044fb1fc72dd026d98917bbf9b9680fd9d3`

## Remaining observations, not blockers for this completed task
- Walmart still frequently returns no usable current price on both official exact paths (`api_price_missing->api_price_missing`). That is external-data / future price-recovery quality work, not recurrence of the delivery stall.
- Occasional Product Lookup HTTP 400 diagnostics remain isolated per item and fail closed.
- The `discord.gateway Can't keep up` warnings remain backlog task `SP-GATEWAY-EVENT-LOOP-LAG`; do not mix that work into Walmart without an explicit task switch.

## Safety invariants preserved
- exact Walmart item identity remains mandatory;
- relevance-search neighbors cannot donate price, seller, reference, or offer proof;
- installment-payment values are not current-price proof;
- seller, selected offer, variant, marketplace, condition, fulfillment, shipping/delivered-price checks remain fail-closed;
- no catalog price is promoted merely because detail/Product Lookup omitted a price;
- reference-price trust policy and guild discount thresholds are unchanged;
- #277 six-hour confirmed-no-price retry behavior is unchanged;
- #280 pressure-lane classification is unchanged;
- #281 exact claim ranking is unchanged;
- #282 excludes only mature confirmed no-price rows from split-aware emergency pressure;
- page challenge/pacing behavior, scheduled recheck floors, lease safety, durable fanout, public thresholds, DM filtering, categories, and dedupe remain unchanged.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's explicit FORCE SWITCH. Preserve the merged movie-ticket work and production-acceptance state; do not resume it without another explicit FORCE SWITCH.

## Backlog — not active
- SP-GATEWAY-EVENT-LOOP-LAG
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
No Walmart implementation task is active. Do not start another backlog item until the user explicitly switches tasks under the Single Active Task Lock.
