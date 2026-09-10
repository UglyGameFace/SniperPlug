# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: recover exact current/reference prices when Walmart supplies valid same-item data, keep every item/seller/offer/variant/shipping guard fail-closed, prevent known low-signal retry populations from starving catalog discovery, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue pressure/claim → fresh proof/API detail → exact-ID Affiliate Product Lookup recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**PR #279 validated and merged. Production still shows catalog starvation, but the supplied post-#279 window now isolates the next scheduler bug: mature confirmed dual-no-price retries are still counted as fresh/retry pressure, so their six-hour retry waves can repeatedly trip catalog backpressure even though #277 correctly cooled them. PR #280 is active to split that known low-signal lane from genuine fresh pressure without dropping or weakening any retry/proof path.**

Relevant ancestry:
- PR #279 validated head: `5304e3ec7e70a71b9210b8a35f4b49e15d451270`
- PR #279 validation: **1,411 passed**, targeted/native-libsql/compile/import/pip/diff/scope/conflict/clean-head checks passed
- PR #279 merge: `54f1a84b42b6c0f0cec7f8182483994efb918efd`
- PR #278 merge: `15919739996093a1cb55361ee5539e96a7aebb3f`
- PR #277 merge: `a449bd43ea319f73c63c4604e16a4220225b7ec5`
- PR #276 merge: `62889044fb1fc72dd026d98917bbf9b9680fd9d3`

## Proven production behavior after #279
The supplied Sep 10 production window repeatedly shows:
- `confirmed_no_price_cooldown=api_price_missing_v277` live;
- exact batches still dominated by `api_price_missing->api_price_missing`;
- `fresh/retry pressure` repeatedly above the catalog pause threshold of `12` (examples include 49, 26, 29, 55, and 36);
- `Global Walmart catalog discovery paused by fresh exact-detail backpressure active` continuing;
- page recovery remaining separately saturated at `>=64`, while scheduled rechecks are `0`;
- no evidence in this excerpt of proof gates being bypassed or page failures replacing the API no-price problem.

The absence of `catchup=legacy_no_price_multibatch_v279` from this later excerpt does **not** prove #279 is absent. That marker is emitted only by the bounded compatibility pass, not by every exact write telemetry line. The periodic v277 marker alone is therefore not a valid deployment discriminator for #279.

## Root cause isolated for #280
`load_walmart_exact_queue_pressure()` currently puts every due `retry` row into `initial_due_now`. `catalog_backpressure_reason()` treats `initial_due_now >= 12` as fresh intake pressure and `should_use_drain_mode()` treats `initial_due_now >= 48` as a reason for 24/4 drain mode.

A confirmed dual-no-price retry is materially different from a genuinely fresh or ordinary retry row: both official exact price paths already returned no usable current price and #277 deliberately scheduled the row six hours out. When a large population reaches that six-hour boundary, the rows become due again and are incorrectly reclassified as “fresh/retry pressure.” That can pause the catalog before it creates new pending work, even though the rows are not new discovery pressure at all.

This is a scheduler classification bug, not a reason to increase cooldowns again or weaken current-price proof.

## Draft PR #280 — separate confirmed no-price pressure lane
Branch: `fix/walmart-no-price-pressure-lane-280`
Base: `54f1a84b42b6c0f0cec7f8182483994efb918efd`

### Intended behavior
- keep confirmed dual-no-price rows fully retryable and included in total actionable due pressure;
- report a dual-no-price retry separately as `confirmed_no_price_due_now` only while its `next_attempt_at` still represents at least the full #277 cooldown from `last_attempt_at`;
- use the preserved cooldown schedule as the classification signal because #277 deliberately keeps that schedule across weak/cosmetic rediscovery but shortens it for a complete fresh snapshot or material apparent current/reference change;
- keep any shortened/woken dual-no-price retry in `initial_due_now` until exact re-verification updates its state, so new proof remains genuine fresh pressure;
- use `initial_due_now` as the genuine fresh/pending/ordinary-retry lane for the 12-row catalog pause and 48-row drain thresholds;
- keep page recovery and scheduled rechecks separate exactly as before;
- retain conservative legacy-unsplit behavior for callers that do not expose the new lane;
- preserve total emergency catalog backpressure: the catalog-facing confirmed-no-price lane may count up to the existing 600-row emergency cap, while the exact worker's explicit `cap=64` still keeps its own read bounded;
- preserve #277 six-hour retry delay and fresh/material rediscovery wake behavior;
- expose `confirmed no-price retry` in bounded pressure/health telemetry so production can prove whether mature no-price waves are still the thing occupying the queue.

### Files
- `sniperplug/services/walmart_exact_queue_health.py`
- `sniperplug/services/walmart_exact_queue_pressure.py`
- `sniperplug/services/walmart_fresh_work_policy.py`
- `tests/test_walmart_exact_queue_pressure.py`
- `tests/test_walmart_confirmed_no_price_pressure_lane_280.py`
- `tests/test_global_autoscan_architecture.py`
- `ACTIVE_TASK.md`

## Safety invariants
Unchanged:
- exact Walmart item identity is mandatory;
- relevance-search neighbors cannot donate price, seller, reference, or offer proof;
- installment-payment values are not current-price proof;
- seller, selected offer, variant, marketplace, condition, fulfillment, shipping/delivered-price checks remain fail-closed;
- no catalog price is promoted merely because detail/Product Lookup omitted a price;
- reference-price trust policy and guild discount thresholds are unchanged;
- page challenge circuit, HTML pacing, dedupe, categories, durable fanout, and DM behavior are unchanged;
- ordinary transient retry behavior remains unchanged;
- confirmed no-price rows are not deleted, quarantined, marked terminal, or skipped by the exact worker;
- weak/cosmetic rediscovery does not turn a mature confirmed-no-price row back into fresh pressure merely because a catalog timestamp changed;
- a #277 fresh/material wake that shortens the no-price schedule remains genuine fresh pressure;
- #278/#279 legacy catch-up behavior remains unchanged.

## Validation / results
The first full-suite run on exact head `167ebdf6ee8392c732908ae2f26705637f4220b9` reached **1,417 passed / 1 failed**. The sole failure was `test_health_counts_only_actively_leased_verifying_rows`: its static SQL-parameter shape assertion still expected the pre-#280 14 parameters, while the new separate no-price due lane correctly adds two bound `now` parameters for `next_attempt_at` and lease expiry. The production query behavior itself was not the failing condition. The regression has been updated to assert the new 16-parameter shape and the new lane explicitly.

Fresh exact-head validation is required after the correction.

Do not merge until all of the following pass on one exact head:
- new #280 pressure-lane behavioral/integration regressions, including preserved-cooldown classification, shortened fresh/material wake behavior, and emergency-pressure headroom;
- existing pressure/fresh-work-policy/drain regressions;
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

## Production acceptance after #280
Require production logs to show the new split explicitly:
- `new/retry` reflects genuine pending/ordinary retry pressure plus dual-no-price rows whose schedule was shortened by a fresh/material wake;
- `confirmed no-price retry` reports mature dual-no-price rows still on the preserved full cooldown schedule;
- a large mature confirmed-no-price due wave by itself no longer triggers `fresh exact-detail backpressure active` or drain mode;
- genuine fresh pressure at 12 still pauses catalog and at 48 still permits drain mode;
- total emergency pressure still remains a catalog safety stop;
- catalog discovery resumes when genuine fresh pressure is below threshold;
- newly discovered/materially changed items still wake and outrank mature retry work through the existing queue ordering;
- qualifying exact/fresh proof continues through verification, durable fanout, public/DM delivery, and guild thresholds.

Do not call the Walmart task complete from tests alone. Production must prove catalog discovery is no longer being starved by mature confirmed-no-price retry waves.

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
Run fresh exact-head validation after the #280 test-shape correction, merge only that validated SHA, deploy canonical `main`, then judge production by the separated `new/retry` versus `confirmed no-price retry` pressure instead of treating every due retry as fresh work.
