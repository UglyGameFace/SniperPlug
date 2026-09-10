# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: recover exact current/reference prices when Walmart supplies valid same-item data, keep every item/seller/offer/variant/shipping guard fail-closed, drain the actionable exact queue normally, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue claim → fresh proof/API detail → exact-ID Affiliate Product Lookup recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**PR #276 is validated, merged, and proven live. Its Product Lookup → relevance Search handoff is closed. Production then proved the dominant remaining blocker is a confirmed dual no-price population whose retry cooldown is being defeated by ordinary catalog rediscovery. PR #277 is active to cool only that proven population while preserving immediate wake-up on genuinely new proof.**

Relevant ancestry:
- PR #276 validated head: `019251c819a480335f3f3acb097035c688e841db`
- PR #276 merge: `62889044fb1fc72dd026d98917bbf9b9680fd9d3`
- PR #275 validated head: `8ce958d7bcf0ef8f9125bf1c12762aaf444e3245`
- PR #275 merge: `4a4f6462bb3f3689243362abfdc23531c2aeff43`
- PR #274 validated head: `30a82f7c6e83e520d199d3008729907bb5ddd3f1`
- PR #274 merge: `d7c466851d956cffa336224d218e6f3339c2008e`
- PR #270 validated head: `5e48002743fdae819d147fa7b79b21fc288d5ba1`
- PR #270 merge: `33e82775cd06aef46f023e91da0f5a6177e2cbad`
- #257 merge: `4d7646dd0b4d120c4c994d60b3d78b6c08760c53`

## Production evidence through #276
The Sep 9 live process emitted:

`price_recovery=exact_id_search_v257`

`exact_lookup=affiliate_items_ids_v276`

so the current Product Lookup recovery build is definitely deployed.

After #276, the old Product Lookup recovery leak into relevance Search disappeared from the supplied post-deploy batches: `api_exact_search_no_match` was no longer the recurring outcome. The dominant result became:

`api_price_missing->api_price_missing`

That means the primary exact detail route and the exact-ID Product Lookup recovery both failed to provide a canonical numeric current price for the same claim.

The bounded #275 diagnostic then captured a matched exact Product Lookup item whose price-like shape contained a textual `priceInformation` instruction to visit Walmart.com and an `installmentPaymentPrice`, with no selected-offer container. The installment amount is financing context, not the item's current purchase price, so it must not be promoted. No guessed price alias is justified from that evidence.

At the same time:
- actionable queue pressure repeatedly returned to roughly 95-129 rows;
- fresh/retry pressure repeatedly crossed the catalog pause threshold;
- catalog discovery was repeatedly paused even though catalog-driven fresh proof had already proven capable of producing verified rows and markdowns;
- occasional fully proven exact rows still verified and reached normal fanout/threshold filtering, so canonical verification, persistence, durable event creation, and fanout are not globally broken.

## Root cause isolated for #277
The remaining loop is scheduling, not price parsing.

`_failure_storage_policy` treated a claim whose primary detail and Product Lookup both returned `api_price_missing` as an ordinary transient retry. That starts at the normal short retry cadence and only backs off gradually.

Separately, the bulk catalog enqueue path intentionally re-arms ordinary `retry` rows by pulling `next_attempt_at` toward the incoming catalog row's `now`. That is useful for transient failures, but it defeats backoff for the newly proven dual no-price population. Repeated rediscovery therefore wakes the same no-price items again, keeps fresh/retry pressure hot, drives drain mode, and starves healthy catalog intake.

The fix must be narrow: do not change generic retry behavior and do not create a new proof source.

## PR #277 implementation — confirmed no-price cooldown without proof loss
Branch: `fix/walmart-confirmed-no-price-backoff-277`

### `sniperplug/services/walmart_exact_verification_queue.py`
- recognizes only the proven dual signature containing both primary `retry_kind=api_price_missing` and `recovery_kind=api_price_missing`;
- gives that population the existing queue retry ceiling of six hours immediately instead of repeating the same exact API work every few minutes;
- leaves single/transient `api_price_missing`, HTTP failures, timeouts, provider-unavailable handling, page retry policy, and ordinary exponential retry behavior unchanged;
- adds no new status and no new proof path.

### `sniperplug/services/walmart_exact_verification_queue_bulk.py`
- loads the existing row's bounded `last_error` so rediscovery can distinguish the proven dual no-price population from ordinary retries;
- ordinary weak/cosmetic rediscovery may still update metadata but cannot shorten a confirmed no-price row's cooldown;
- a complete fresh catalog proof snapshot still wakes the row immediately;
- a material apparent current-price or reference-price change still wakes the row immediately;
- ordinary retry rows keep their historical rearm behavior.

### `sniperplug/services/walmart_exact_queue_bulk_runtime.py`
- preserves `exact_lookup=affiliate_items_ids_v276`;
- adds `confirmed_no_price_cooldown=api_price_missing_v277` so production can prove the scheduling fix is deployed.

### Regression coverage
`tests/test_walmart_confirmed_no_price_cooldown_277.py` verifies:
- dual no-price failures use the existing retry ceiling;
- a single `api_price_missing` keeps the normal transient backoff;
- unchanged weak rediscovery does not wake a cooled row;
- cosmetic metadata can persist without breaking the cooldown;
- a complete fresh proof snapshot wakes the row;
- a material catalog current-price change wakes the row.

`tests/test_walmart_confirmed_no_price_cooldown_277_marker.py` verifies the live deployment marker remains in the bulk runtime.

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
- only the proven dual no-price scheduling population gets the existing maximum retry delay, and genuinely new fresh proof still wakes it immediately.

## Validation / results
PR #276 passed exact-head validation on `019251c819a480335f3f3acb097035c688e841db` in the known-good Termux/Ubuntu Python 3.12.14 environment:
- the initial targeted pass exposed one stale #274 deployment-marker assertion and no behavioral regression;
- after updating that intentionally superseded assertion, the final exact-head validation passed;
- full pytest: **1,392 passed, 1 existing `audioop` deprecation warning**;
- targeted Walmart regressions passed;
- native libsql process tests passed;
- `pip check` reported no broken requirements;
- compileall passed;
- import smoke passed;
- changed-file scope matched exactly the five intended #276 files;
- conflict-marker scan was clean;
- validation worktree was clean;
- final exact HEAD matched the expected SHA.

PR #276 was then merged at `62889044fb1fc72dd026d98917bbf9b9680fd9d3`, deployed, and production proved `exact_lookup=affiliate_items_ids_v276`. Its search-handoff acceptance is complete.

PR #277 validation is pending on its exact head. Do not merge until the new cooldown regressions, the existing catalog queue/retry regressions, #276/#274/#257/page identity regressions, native libsql tests, compile/import/pip checks, full pytest, diff/scope checks, conflict-marker scan, clean worktree, and final exact-head SHA recheck pass.

## Production acceptance after #277
After validation/merge/deploy, require:
- `confirmed_no_price_cooldown=api_price_missing_v277` in live write telemetry;
- repeated `api_price_missing->api_price_missing` rows stop returning immediately to the due queue and receive the six-hour cooldown;
- fresh/retry pressure falls enough for catalog discovery to resume instead of remaining pinned by unchanged no-price rows;
- a complete fresh catalog snapshot or material catalog price/reference change still wakes the affected row promptly;
- ordinary transient retry behavior remains intact;
- qualifying exact/fresh proof continues to verify and flow through unchanged public/DM fanout and guild threshold logic.

Do not call the Walmart task complete from unit tests alone. Production acceptance must show the hot no-price loop is broken without suppressing newly actionable proof.

## Blockers / risks
- ChatGPT has repository access but no Discloud deployment/runtime connector, so live deploy and production acceptance remain external.
- Walmart Affiliate response shapes are external and may vary; every proof gate remains fail-closed.
- Separate `discord.gateway Can't keep up` warnings were observed while the Walmart queue was hot. That remains backlog task `SP-GATEWAY-EVENT-LOOP-LAG` and is not being mixed into #277.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's explicit FORCE SWITCH.

Preserved state:
- #265, #266, #267 merged and validated;
- #268 validated and merged at `e970b2dcb5854da06c25c9a9e9b42476975584fa`;
- PR #271 merged Gofobo truthfulness at `60cc3f8b55d2543cfa396105f189ec2427462ca7`;
- PR #272 merged movie result embed-budget protection at `fe0558ea7795d773e0f17e52f8284153711a3b36`;
- remaining movie step is production acceptance on the existing July 31 panel;
- unrelated draft PR #273 remains untouched while Walmart is active;
- movie work must resume from its preserved acceptance state, not from reimplementation.

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
Validate draft PR #277 on its exact head. If every local gate passes, mark it ready, merge with an exact-head guard, deploy canonical `main`, and accept it only after live telemetry proves the confirmed no-price population cools without blocking fresh proof or verified-deal fanout.
