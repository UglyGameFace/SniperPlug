# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: recover exact current/reference prices when Walmart supplies valid same-item data, keep every item/seller/offer/variant/shipping guard fail-closed, prevent stale no-price retry populations from starving catalog discovery, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue claim → fresh proof/API detail → exact-ID Affiliate Product Lookup recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**PR #278 validated and merged, but the supplied post-merge production window still shows fresh/retry pressure repeatedly above the catalog pause threshold and exact batches dominated by `api_price_missing->api_price_missing`. PR #279 is active to remove the remaining bounded-catch-up limitation without weakening any verification rule.**

Relevant ancestry:
- PR #278 validated head: `3b3baaeb0048b9e63c967f0a64a089165f0d7527`
- PR #278 validation: **1,407 passed, 1 warning**, native libsql/compile/import/pip/diff/scope/conflict/clean-head checks passed
- PR #278 merge: `15919739996093a1cb55361ee5539e96a7aebb3f`
- PR #277 validated head: `e29b417b62b8f2636d6094f917f732016e71f8fc`
- PR #277 merge: `a449bd43ea319f73c63c4604e16a4220225b7ec5`
- PR #276 merge: `62889044fb1fc72dd026d98917bbf9b9680fd9d3`

## Proven production behavior
PR #277's live write marker is present:

`confirmed_no_price_cooldown=api_price_missing_v277`

The supplied Sep 10 production window still shows repeated drain batches of 24 dominated by confirmed dual no-price results and fresh/retry pressure such as `44`, `33`, `45`, `59`, and `48` against the catalog pause threshold of `12`. Catalog discovery continues to report backpressure pauses.

The supplied excerpt does not contain the #278 startup-only marker, so that excerpt alone cannot prove the concrete startup `cooled=<count>`. Regardless, inspection of merged #278 exposes a structural catch-up ceiling that can leave a larger legacy population behind.

## Remaining root cause isolated for #279
PR #278 deliberately normalized at most **200** qualifying pre-#277 rows inside `ensure_exact_runtime_schema_once()`.

On merged main, `_RuntimeState.schema_ready` causes every later `ensure_exact_runtime_schema_once()` call on the same connection to return immediately. Therefore the compatibility normalizer can run only one 200-row slice for the lifetime of that connection. If more than 200 qualifying legacy retry rows exist, the remainder retain their old short schedules and can continue maturing in waves after the first slice is fixed.

This is a bounded-catch-up completion bug, not a reason to weaken price or identity proof.

## Draft PR #279 — bounded multi-batch legacy cooldown catch-up
Branch: `fix/walmart-legacy-cooldown-multibatch-279`

### `sniperplug/services/walmart_exact_runtime_schema.py`
- preserve one-time schema/index initialization;
- retain the same **200-row maximum per compatibility pass**;
- remember per-connection catch-up state separately from schema-ready state;
- after a positive compatibility pass, allow exactly one additional bounded pass on the next exact-worker call;
- continue one bounded pass per worker call until a pass changes **zero** rows;
- once a zero-change pass proves catch-up empty, set an in-memory completion flag so steady-state worker calls perform no further legacy query;
- do not infer completion merely from `cooled < 200`, because races can reduce an update count while additional qualifying rows remain;
- preserve the original #278 predicates protecting active leases and post-attempt rediscovery;
- preserve the #278 marker and add `catchup=legacy_no_price_multibatch_v279`, plus `complete=<true|false>` and `batch=<n>` for direct production proof.

### Regression coverage
`tests/test_walmart_legacy_no_price_multibatch_279.py` proves:
- one 200-row pass does not mark catch-up complete;
- positive follow-up batches continue while schema creation itself remains once-per-connection;
- a zero-change pass is the completion signal;
- after completion, later worker calls no longer query the legacy population;
- a real SQLite queue containing 205 qualifying rows is cooled as 200 + 5 across bounded calls, never as one unbounded migration;
- the #278 marker remains and the #279 catch-up marker is present.

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
- #277 fresh/material rediscovery wake behavior remains unchanged;
- #278 row-eligibility predicates remain unchanged;
- #279 changes only how many **bounded 200-row passes over time** may run before legacy catch-up is declared complete.

## Validation / results
PR #279 exact-head validation is pending. Do not merge until all of the following pass:
- new #279 behavioral/integration/marker regressions;
- #278 and #277 cooldown regressions;
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

## Production acceptance after #279
Require:
- runtime telemetry shows `catchup=legacy_no_price_multibatch_v279`;
- if qualifying legacy rows exceed one slice, successive bounded logs show additional batches rather than silently stopping after batch 1;
- catch-up eventually reports `cooled=0 complete=true` and then disappears from steady-state calls;
- #277 marker remains present;
- fresh/retry pressure falls below `12` when genuinely new work is not arriving;
- catalog discovery resumes normally;
- complete fresh snapshots and material current/reference changes still wake affected rows promptly;
- ordinary transient retries remain unchanged;
- qualifying exact/fresh proof continues through verification, durable fanout, public/DM delivery, and guild thresholds.

Do not call the Walmart task complete from unit tests alone. Production still has to prove that stale compatibility work stops starving discovery without suppressing newly actionable proof.

## Blockers / risks
- ChatGPT has repository access but no Discloud deployment/runtime connector, so live deploy and production acceptance remain external.
- Walmart Affiliate response shapes are external and may vary; every proof gate remains fail-closed.
- Separate `discord.gateway Can't keep up` warnings remain backlog task `SP-GATEWAY-EVENT-LOOP-LAG` and are not being mixed into this PR.

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
Review PR #279 diff, run exact-head validation in the known-good Ubuntu/proot Python environment, merge only on the validated SHA, deploy canonical `main`, then judge production by the multi-batch catch-up markers and fresh/retry pressure rather than by raw `api_price_missing` counts alone.
