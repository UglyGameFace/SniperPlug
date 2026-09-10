# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: recover exact current/reference prices when Walmart supplies valid same-item data, keep every item/seller/offer/variant/shipping guard fail-closed, drain the actionable exact queue normally, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue claim → fresh proof/API detail → exact-ID Affiliate Product Lookup recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**PR #277 is validated, merged, deployed, and its capability marker is proven live. Production then exposed one compatibility gap: rows that already had a confirmed dual-no-price failure before #277 can retain their old short future retry timestamps until each one becomes due. Draft PR #278 is active to normalize only that legacy schedule population at startup without delaying any post-attempt catalog evidence.**

Relevant ancestry:
- PR #277 validated head: `e29b417b62b8f2636d6094f917f732016e71f8fc`
- PR #277 merge: `a449bd43ea319f73c63c4604e16a4220225b7ec5`
- PR #276 validated head: `019251c819a480335f3f3acb097035c688e841db`
- PR #276 merge: `62889044fb1fc72dd026d98917bbf9b9680fd9d3`
- PR #275 merge: `4a4f6462bb3f3689243362abfdc23531c2aeff43`
- PR #274 merge: `d7c466851d956cffa336224d218e6f3339c2008e`
- PR #270 merge: `33e82775cd06aef46f023e91da0f5a6177e2cbad`
- #257 merge: `4d7646dd0b4d120c4c994d60b3d78b6c08760c53`

## What #277 fixed
The dominant production failure after #276 was `api_price_missing->api_price_missing`: both primary exact detail and exact-ID Product Lookup returned no canonical current purchase price.

PR #277:
- recognizes only `retry_kind=api_price_missing` plus `recovery_kind=api_price_missing`;
- stores new occurrences at the existing six-hour queue retry ceiling;
- preserves that cooldown across ordinary weak/cosmetic rediscovery;
- still wakes the row immediately for a complete fresh catalog proof snapshot or material apparent current/reference price change;
- leaves ordinary transient retry behavior and every proof gate unchanged;
- added `confirmed_no_price_cooldown=api_price_missing_v277` to live write telemetry.

Exact-head validation passed on `e29b417b62b8f2636d6094f917f732016e71f8fc` in the known-good Termux/Ubuntu environment, including **1,399 passed** full pytest, targeted Walmart regressions, native libsql process tests, compileall, import smoke, `pip check`, diff/scope, conflict-marker, clean-worktree, and final exact-head checks.

PR #277 merged at `a449bd43ea319f73c63c4604e16a4220225b7ec5`, and production emitted `confirmed_no_price_cooldown=api_price_missing_v277`, so the validated code is definitely deployed.

The post-deploy process still showed fresh/retry waves above the catalog pause threshold. Supplied live examples included `39/12`, `36/12`, and `32/12`, with repeated 24/4 drain batches dominated by `api_price_missing->api_price_missing`. Catalog discovery therefore remained paused.

## Compatibility gap isolated for #278
The #277 six-hour policy is applied when a failure outcome is persisted. It does not rewrite a row that already had the same confirmed dual-no-price `last_error` before deployment but still carries a pre-#277 `next_attempt_at` calculated from the old short exponential schedule.

Those future timestamps survive restart and can mature in waves after #277 is live. Each row gets the correct six-hour delay only after it becomes due and is processed again. This structurally explains fresh/retry pressure refilling even while catalog intake is paused.

The compatibility fix must stay bounded and must not delay genuinely new catalog evidence.

## Draft PR #278 — bounded legacy confirmed-no-price floor
Branch: `fix/walmart-confirmed-no-price-legacy-cooldown-278`

### `sniperplug/services/walmart_confirmed_no_price_legacy_cooldown.py`
Adds one bounded compatibility normalizer selecting at most **200** rows, keeping the four-values-per-row update plus guard parameters below SQLite's conservative historical variable ceiling. A row qualifies only when:
- status is `retry`;
- persisted `last_error` contains both confirmed `api_price_missing` markers;
- `last_attempt_at` exists;
- current `next_attempt_at` is earlier than `last_attempt_at + 6 hours`;
- that six-hour floor has not already elapsed;
- `last_seen_at <= last_attempt_at`, so no catalog rediscovery newer than the confirming exact attempt can be delayed;
- no active lease exists.

For accepted rows, the target is exactly `last_attempt_at + 6 hours`, not deployment time plus six hours. The migration does not restart the clock. It uses one bounded bulk `UPDATE ... RETURNING` and does not alter status, error, proof, identity, prices, snapshots, or attempt counts.

### `sniperplug/services/walmart_exact_runtime_schema.py`
The normalizer runs inside the existing once-per-connection exact-runtime schema gate after the queue/indexes exist and before the initialization commit. It is not a recurring worker task.

Startup telemetry emits:

`marker=legacy_no_price_floor_v278 cooled=<count> limit=200`

so deployment and migration effect are directly observable.

### Regression coverage
- `tests/test_walmart_confirmed_no_price_legacy_cooldown_278.py`: exact six-hour floor, post-attempt rediscovery safety, active-lease safety, elapsed-floor safety, already-correct schedule safety, and unrelated transient retry safety.
- `tests/test_walmart_confirmed_no_price_legacy_cooldown_278_runtime.py`: the real exact-runtime once gate applies the migration and does not restart it on a second call.
- `tests/test_walmart_confirmed_no_price_legacy_cooldown_278_marker.py`: startup migration wiring and deployment marker are guarded.

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
- #277's fresh/material rediscovery wake path remains unchanged;
- #278 only lengthens an unambiguously stale pre-#277 schedule to the same six-hour floor #277 already applies to new occurrences.

## Validation / results
PR #278 validation is pending on its exact head. Do not merge until all of the following pass:
- new #278 behavioral/runtime/marker regressions;
- #277 confirmed-no-price regressions;
- #276/#274/#257 Product Lookup and recovery regressions;
- existing Walmart queue, drain, pressure, pacing, fresh-proof, delivery-stall, and page-identity regressions;
- native libsql process tests;
- compileall;
- import smoke;
- `pip check`;
- full pytest;
- diff/scope check;
- conflict-marker scan;
- clean worktree;
- final exact-head SHA recheck.

## Production acceptance after #278
After validation/merge/deploy, require:
- startup telemetry contains `marker=legacy_no_price_floor_v278` with a concrete cooled count;
- #277 marker remains present in exact write telemetry;
- the old confirmed no-price population no longer refills fresh/retry pressure merely because pre-#277 timers mature;
- fresh/retry pressure falls below the `12` catalog pause threshold when no genuinely new work is arriving;
- catalog discovery resumes normally;
- complete fresh snapshots and material current/reference changes still wake affected rows promptly;
- ordinary transient retries remain unchanged;
- qualifying exact/fresh proof continues through unchanged verification, durable fanout, public/DM delivery, and guild thresholds.

Do not call the Walmart task complete from unit tests alone. Production still has to prove the hot queue stops starving discovery without suppressing newly actionable proof.

## Blockers / risks
- ChatGPT has repository access but no Discloud deployment/runtime connector, so live deploy and production acceptance remain external.
- Walmart Affiliate response shapes are external and may vary; every proof gate remains fail-closed.
- Rows with catalog rediscovery newer than their last exact attempt are intentionally excluded from #278 because the migration cannot prove whether that rediscovery was cosmetic or material. Those rows remain governed by normal #277 processing.
- Separate `discord.gateway Can't keep up` warnings remain backlog task `SP-GATEWAY-EVENT-LOOP-LAG` and are not being mixed into this PR.

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
Validate draft PR #278 on its exact head in the known-good Python environment. If every gate passes, merge with an exact-head guard, deploy canonical `main`, and accept only after startup plus live queue telemetry proves the legacy schedule wave is gone without blocking fresh proof or verified-deal fanout.
