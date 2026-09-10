# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: recover exact current/reference prices when Walmart supplies valid same-item data, keep every item/seller/offer/variant/shipping guard fail-closed, prevent known low-signal retry populations from starving catalog discovery or exact verification, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue pressure/claim → fresh proof/API detail → exact-ID Affiliate Product Lookup recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**PR #280 validated, merged, and production-proven for its intended pressure split. The supplied post-#280 window also isolates the next scheduler bug: mature confirmed dual-no-price retries are now reported separately, but the exact claim selector still mixes them into the same `retry_candidates` slice as ordinary retries. Because that slice orders by priority/last-seen, the low-signal no-price population can consume entire 8/24-item batches while `new/ordinary-retry` remains above the catalog pause threshold. PR #281 is active to preserve the #280 split at claim time.**

Relevant ancestry:
- PR #280 validated head: `bbd2c5f4679d4fc5ea00b74c215f61117c9efad3`
- PR #280 validation: targeted **48 passed**, native libsql **10 passed**, full suite **1,418 passed**, compile/import/pip/diff/scope/conflict/clean-head checks passed
- PR #280 merge: `ab0b8b8cc1c66724420843cebcca4e414137a856`
- PR #279 merge: `54f1a84b42b6c0f0cec7f8182483994efb918efd`
- PR #278 merge: `15919739996093a1cb55361ee5539e96a7aebb3f`
- PR #277 merge: `a449bd43ea319f73c63c4604e16a4220225b7ec5`
- PR #276 merge: `62889044fb1fc72dd026d98917bbf9b9680fd9d3`

## Production evidence after #280
The Sep 10 post-restart window proves #280 is live and materially improved the system:
- bounded pressure now explicitly reports `new/retry`, `confirmed no-price retry`, `page recovery`, and scheduled rechecks separately;
- startup compatibility normalization completed cleanly: `legacy_no_price_floor_v278 catchup=legacy_no_price_multibatch_v279 cooled=0 ... complete=true`;
- with `new/retry=8` and `confirmed no-price retry=60`, catalog discovery was allowed to run instead of being falsely paused by the mature no-price wave;
- subsequent catalog batches returned **43** and **98** usable item IDs, including **1** and **17** safe fresh proofs;
- one 24-item exact batch verified **17** rows, observed **9** official was-prices and **9** markdowns, and persisted **17** offer-memory rows;
- durable fanout produced **5** exact cards and processed all five events through both configured public guild destinations; the examples were correctly not posted because their discounts were below the configured 40%/50% guild thresholds, proving the fanout/threshold path is functioning rather than silently dead.

The same window then exposes the remaining claim-order problem:
- `confirmed no-price retry` rises through **60 → 113 → 177 → 253 → 322 → 385 → 439** as six-hour retry waves mature;
- `new/ordinary-retry due` remains repeatedly above the 12-row catalog pause threshold, including **64, 27, 32, 33**;
- while that genuine fresh/ordinary pressure remains, worker batches repeatedly contain **8/8** or **24/24** `api_price_missing->api_price_missing` outcomes;
- catalog therefore resumes correctly when fresh pressure is low, but can pause again while the worker spends capacity on the mature low-signal lane instead of clearing the ordinary retry rows that actually drive the pause.

## Root cause isolated for #281
`claim_due_rows_batched()` uses the indexed candidate-pool selector. Fresh snapshots are correctly first, then nonfresh statuses are split into bounded CTEs. However all nonfresh `status='retry'` rows share one `retry_candidates` CTE and one status rank. Inside that CTE the bounded slice is ordered only by `priority_score DESC, last_seen_at DESC`.

#280 changed pressure classification, not this claim ordering. A mature confirmed dual-no-price row can therefore be excluded from `initial_due_now` for policy purposes yet still outrank an ordinary retry inside `retry_candidates`. Weak/cosmetic rediscovery is allowed to refresh discovery metadata without shortening the #277 cooldown, so mature no-price rows can also have competitive recent metadata while remaining low-signal. The production result is exactly what the logs show: ordinary retry pressure pauses catalog intake while mature no-price rows consume most exact-worker claims.

This is a claim-priority bug, not evidence that #280 failed and not a reason to weaken proof or discard no-price retries.

## Draft PR #281 — preserve no-price pressure split at claim time
Branch: `fix/walmart-no-price-claim-priority-281`
Base: `ab0b8b8cc1c66724420843cebcca4e414137a856`

### Intended behavior
- keep fresh discovery snapshots first exactly as today;
- keep `pending`, ordinary `retry`, `failed`, page recovery, and scheduled verified rechecks ahead of mature confirmed dual-no-price retries;
- within the existing bounded indexed `retry_candidates` seek, assign the mature dual-no-price/full-#277-cooldown shape a low-signal rank instead of the ordinary retry rank;
- order the retry CTE by that rank before priority/last-seen, so ordinary retries fill the bounded retry slice first;
- do **not** add a hard no-price claim cap: if higher-signal work does not fill the batch, mature no-price rows fill the unused capacity and remain fully retryable;
- keep a dual-no-price row whose schedule was shortened by #277 fresh/material wake logic in the ordinary retry rank;
- preserve the same lane count, SQL parameter count, runtime indexes, lease transaction, page cap, verified-age floors, and bounded read-amplification shape.

### Files
- `sniperplug/services/walmart_exact_queue_drain.py`
- `tests/test_walmart_no_price_claim_priority_281.py`
- `ACTIVE_TASK.md`

## Safety invariants
Unchanged:
- exact Walmart item identity is mandatory;
- relevance-search neighbors cannot donate price, seller, reference, or offer proof;
- installment-payment values are not current-price proof;
- seller, selected offer, variant, marketplace, condition, fulfillment, shipping/delivered-price checks remain fail-closed;
- no catalog price is promoted merely because detail/Product Lookup omitted a price;
- reference-price trust policy and guild discount thresholds are unchanged;
- #277 six-hour confirmed-no-price retry behavior remains unchanged;
- #280 pressure classification and emergency backpressure remain unchanged;
- confirmed no-price rows are not deleted, quarantined, marked terminal, or made unclaimable;
- a #277 fresh/material wake stays high-priority ordinary work;
- page challenge/pacing behavior, scheduled recheck floors, lease safety, durable fanout, public thresholds, DM filtering, categories, and dedupe remain unchanged.

## New #281 regression coverage
`tests/test_walmart_no_price_claim_priority_281.py` proves:
- ordinary retries beat mature confirmed dual-no-price retries even when the no-price rows have much higher priority scores and newer last-seen metadata;
- mature no-price retries fill otherwise-unused batch slots, so the lane is deprioritized rather than starved;
- a dual-no-price row with a shortened schedule remains in the ordinary retry lane and beats a mature full-cooldown row.

## Validation / Definition of Done
Do not merge until one exact #281 head passes:
- new #281 claim-priority regressions;
- #280 pressure-lane regressions;
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

## Production acceptance after #281
Require a fresh deployment from canonical `main` and verify:
- when `new/ordinary-retry due` is nonzero, exact batches stop being monopolized by mature `api_price_missing->api_price_missing` rows;
- genuine fresh/ordinary pressure drains below the 12-row catalog pause threshold so discovery resumes without waiting for the mature no-price population to disappear;
- when higher-signal work is absent, mature no-price retries still continue to be claimed and rescheduled normally;
- fresh/materially changed rows still outrank mature no-price work;
- qualifying exact/fresh proof continues through durable fanout and configured guild thresholds;
- no increase in page failures, proof rejects caused by loosened guards, or Turso read amplification.

Do not call the Walmart task complete from tests alone. Production must prove claim priority now matches the pressure policy.

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
Open PR #281 on the current branch, run exact-head validation, merge only that validated SHA, deploy canonical `main`, then judge production by whether ordinary retry pressure drains before mature confirmed-no-price fill work.