# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: recover exact current/reference prices when Walmart supplies valid same-item data, keep every item/seller/offer/variant/shipping guard fail-closed, prevent known low-signal retry populations from starving catalog discovery or exact verification, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue pressure/claim → fresh proof/API detail → exact-ID Affiliate Product Lookup recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**PR #281 is validated, merged, freshly restarted in production, and its intended ordinary-retry claim priority is no longer the active blocker. Production now isolates the next policy bug: the split-aware 600-row catalog emergency stop still counts mature confirmed dual-no-price retries, so a bounded `confirmed no-price=600` wave plus the separately paced page-recovery lane permanently vetoes catalog discovery even when genuine `new/ordinary-retry` pressure is zero. PR #282 is active to remove only that mature no-price lane from split-aware catalog emergency pressure.**

Relevant ancestry:
- PR #281 validated head: `b35ca77dc2a8c8bc8c44dbe3ebdfadfadbd26d2c`
- PR #281 validation: full suite **1,421 passed**, 1 warning; targeted Walmart regressions, native libsql, compile/import/pip/diff/scope/conflict/clean-head checks passed
- PR #281 merge / current base: `efa9c667e7e96ceaa3553e3e909611b4a6cb024c`
- PR #280 merge: `ab0b8b8cc1c66724420843cebcca4e414137a856`
- PR #279 merge: `54f1a84b42b6c0f0cec7f8182483994efb918efd`
- PR #278 merge: `15919739996093a1cb55361ee5539e96a7aebb3f`
- PR #277 merge: `a449bd43ea319f73c63c4604e16a4220225b7ec5`
- PR #276 merge: `62889044fb1fc72dd026d98917bbf9b9680fd9d3`

## Production evidence after #281 restart
The Sep 10 18:15 restart is a real new process (`pid=1`, fresh Discord login/session, fresh Turso startup readiness), so the following window is valid production evidence against canonical post-#281 code.

Observed repeatedly after restart:
- bounded pressure: `new/retry 0-3`, `confirmed no-price retry >=600`, `page recovery >=64`, scheduled rechecks `0`;
- catalog pause: `exact-detail emergency backpressure active` with totals around `664-673/600` even when `new/ordinary-retry due=0`;
- exact batches remain normal `8/2`, with mature no-price rows filling spare capacity as #281 intended when ordinary work is absent;
- ordinary/fresh work is no longer the persistent cause of the pause: it is usually `0` and briefly `1-3`;
- page recovery advances only intermittently because runtime intentionally sets its claim cap to zero while the one-per-minute HTML probe slot is paced, then allows the bounded page lane when the slot is available;
- verified rows still occasionally emerge (`verified=1`, including unavailable/no-reference/markdown outcomes), so exact verification itself is alive;
- Product Lookup HTTP 400 diagnostics remain isolated per item and do not alter proof gates.

This means #281 did not fail. Its claim-order change addressed ordinary retry starvation. The remaining freeze is now in catalog admission policy.

## Root cause isolated for #282
`catalog_backpressure_reason()` correctly checks genuine fresh/ordinary pressure first at the 12-row threshold. But in split-aware mode its emergency calculation still uses:

`total_pressure = due_now + verifying`

`due_now` includes the mature confirmed dual-no-price lane. The catalog-facing pressure loader intentionally exposes that lane up to 600 rows. Therefore a mature wave at the diagnostic cap can satisfy the **entire 600-row emergency threshold by itself**, even though:
- #277 deliberately put those rows on a six-hour low-signal retry cadence;
- #280 separated them from genuine fresh pressure;
- #281 moved them behind ordinary retries at claim time;
- pausing catalog discovery cannot make the mature no-price lane drain faster.

Production proves the pathological steady state directly: `confirmed no-price=600` + `page recovery=64` + `new=0` still produces `664/600` emergency backpressure indefinitely.

The page-recovery lane should remain part of emergency pressure. Its bounded/paced behavior is legitimate recovery work. The incorrect component is specifically the mature confirmed dual-no-price population that has already completed both official exact price paths and is intentionally being retried as low signal.

## Draft PR #282 — exclude mature no-price rows from split-aware catalog emergency pressure
Branch: `fix/walmart-no-price-emergency-backpressure-282`
Base: `efa9c667e7e96ceaa3553e3e909611b4a6cb024c`

### Intended behavior
- preserve the existing 12-row fresh/ordinary catalog pause exactly;
- keep mature confirmed no-price rows visible in all pressure diagnostics and fully claimable/retryable;
- exclude only mature confirmed no-price rows from the split-aware 600-row emergency veto;
- keep page recovery, scheduled verified rechecks, and active leases in split-aware emergency pressure;
- keep legacy unsplit health on the historical total-backlog emergency behavior because it cannot safely identify the no-price lane;
- do not change exact claim ranking, no-price cooldowns, page pacing/circuit behavior, queue retention, verification proof, or fanout.

### Files
- `sniperplug/services/walmart_fresh_work_policy.py`
- `tests/test_walmart_confirmed_no_price_pressure_lane_280.py` (updates the one production-disproven emergency assumption while preserving the rest of #280 coverage)
- `tests/test_walmart_no_price_emergency_backpressure_282.py`
- `ACTIVE_TASK.md`

## Safety invariants
Unchanged:
- exact Walmart item identity is mandatory;
- relevance-search neighbors cannot donate price, seller, reference, or offer proof;
- installment-payment values are not current-price proof;
- seller, selected offer, variant, marketplace, condition, fulfillment, shipping/delivered-price checks remain fail-closed;
- no catalog price is promoted merely because detail/Product Lookup omitted a price;
- reference-price trust policy and guild discount thresholds are unchanged;
- #277 six-hour confirmed-no-price retry behavior is unchanged;
- #280 pressure-lane classification is unchanged;
- #281 exact claim ranking is unchanged;
- confirmed no-price rows are not deleted, quarantined, marked terminal, or made unclaimable;
- fresh/materially changed no-price rows still classify as genuine ordinary pressure and can pause discovery;
- page challenge/pacing behavior, page recovery emergency pressure, scheduled recheck floors, lease safety, durable fanout, public thresholds, DM filtering, categories, and dedupe remain unchanged.

## New #282 regression coverage
`tests/test_walmart_no_price_emergency_backpressure_282.py` proves:
- the exact production shape `600 mature no-price + 64 page recovery + 0 fresh` does not pause catalog discovery;
- a real 12-row fresh lane still pauses even beside 600 mature no-price rows;
- 600 rows of non-no-price controllable backlog still trigger the emergency stop;
- legacy unsplit health retains its historical total-backlog safety stop.

The existing #280 regression file is revised only where production disproved the old assumption that mature no-price rows should contribute to split-aware emergency pressure. Its queue split, fresh wake, fresh-pressure pause, no-drain, and visibility assertions remain intact.

## Validation / Definition of Done
Do not merge until one exact #282 head passes:
- new #282 backpressure regressions;
- revised #280 pressure-lane regressions;
- #281 claim-priority regressions;
- existing queue drain/index/read-amplification/fresh-work-policy regressions;
- #277/#278/#279 cooldown/catch-up regressions;
- #276/#274/#257 Product Lookup/recovery regressions;
- Walmart queue, pacing, fresh-proof, delivery-stall, fanout regressions;
- native libsql process tests;
- compileall;
- import smoke;
- `pip check`;
- full pytest;
- diff/scope check;
- conflict-marker scan;
- clean worktree;
- final exact-head SHA recheck.

## Production acceptance after #282
Require a fresh deployment from canonical `main` and verify:
- with `confirmed no-price >=600`, `page recovery >=64`, and `new/ordinary-retry <12`, catalog discovery runs instead of logging the old `664/600` emergency pause;
- if genuine fresh/ordinary pressure reaches 12, catalog still pauses normally;
- mature no-price rows continue being claimed in otherwise-unused capacity and keep their six-hour retry behavior;
- page recovery continues its existing bounded/paced progression;
- new catalog batches can again produce safe fresh proof and exact verification/fanout events;
- qualifying deals still obey configured guild thresholds and every proof gate;
- no increase in Turso read amplification or page-request concurrency.

Do not call the Walmart task complete from tests alone. Production must prove catalog intake is no longer vetoed solely by the mature no-price wave.

## Blockers / risks
- ChatGPT has repository access but no Discloud deployment/runtime connector, so live deploy and production acceptance remain external.
- Walmart Affiliate response shapes are external and may vary; every proof gate remains fail-closed.
- The separate `discord.gateway Can't keep up` warnings remain backlog task `SP-GATEWAY-EVENT-LOOP-LAG` and are not being mixed into this Walmart PR.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's explicit FORCE SWITCH. Preserve the merged movie-ticket work and production-acceptance state; do not resume it while Walmart is active.

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
Open PR #282 from the current branch, squash to one exact commit, run exact-head validation, merge only that validated SHA, deploy canonical `main`, then judge production by whether catalog discovery resumes under the mature no-price wave without weakening genuine fresh/emergency safety.