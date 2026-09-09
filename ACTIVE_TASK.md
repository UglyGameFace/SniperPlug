# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: production must prove the recovery-capable Walmart runtime is deployed, expose the exact stage behind any remaining `api_price_missing` failures, recover exact current/reference prices when Walmart supplies valid same-item data, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue claim → fresh proof/API detail → exact-ID Affiliate-search recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**PR #270 is validated and merged. Code validation is complete; the active Walmart task is now at production acceptance.**

Validated PR head: `5e48002743fdae819d147fa7b79b21fc288d5ba1`
PR #270 merge: `33e82775cd06aef46f023e91da0f5a6177e2cbad`
Base/main before merge: `fe0558ea7795d773e0f17e52f8284153711a3b36`
#257 merge: `4d7646dd0b4d120c4c994d60b3d78b6c08760c53`
#257 validated head: `edff57a27fb2e7d342970d0bf2deed782dbbffca`

## Findings / root cause
The authoritative production path is:
1. `walmart_exact_queue_bulk_runtime.py`
2. `fetch_exact_candidate_with_page_pacing()`
3. canonical exact verifier
4. official exact detail
5. on `api_price_missing`, #257 exact-ID Affiliate-search recovery
6. canonical selected-offer/current/reference normalization
7. bulk persistence and fanout.

#257 already attempts exact-ID recovery when official detail has exact identity but no numeric current price. The concrete defect addressed by #270 was observability: if the secondary exact-search recovery also failed, `_recover_api_price_or_reference()` discarded that secondary reason and returned only the original `api_price_missing`. Production therefore could not distinguish exact-search no-match, another no-price payload, candidate-build failure, identity failure, or source unavailability.

## Changes merged in PR #270
### `sniperplug/services/walmart_exact_fetch_pacing.py`
- successful #257 recovery remains unchanged;
- failed recovery preserves the original primary retry/status semantics;
- secondary recovery reason is retained as `recovery_kind=<secondary outcome>`.

### `sniperplug/services/walmart_exact_queue_bulk_runtime.py`
- combined retry labels now surface values such as `api_price_missing->api_exact_search_no_match`;
- exact-write telemetry now includes `price_recovery=exact_id_search_v257`, which is the production deployment fingerprint.

### `tests/test_walmart_exact_price_recovery_257.py`
- covers failed-recovery reason retention;
- preserves primary retry behavior;
- covers combined retry telemetry;
- confirms no HTML recovery is introduced;
- confirms the deployment capability marker remains present.

### Explicitly unchanged
- no current/reference price proof was weakened;
- no item, seller, offer, variant, marketplace, condition, fulfillment, or shipping guard changed;
- no retry delay, queue cadence, page circuit, HTML pacing, threshold, category, dedupe, fanout, or DM rule changed;
- no guessed/catalog fallback price or second verifier was added.

## Validation / results
Exact validated head: `5e48002743fdae819d147fa7b79b21fc288d5ba1`

Validation used the known-good Linux/PRoot Python 3.12 interpreter explicitly because the copied activation script selected Python 3.13 and lacked `libsql`.

Passed:
- targeted Walmart exact/bulk/page-pacing regression set;
- `pip check`;
- compileall;
- import smoke;
- native libsql process tests;
- full pytest: **1,380 passed, 1 existing warning**;
- `git diff origin/main...HEAD --check`;
- changed-file scope exactly `ACTIVE_TASK.md`, `sniperplug/services/walmart_exact_fetch_pacing.py`, `sniperplug/services/walmart_exact_queue_bulk_runtime.py`, `tests/test_walmart_exact_price_recovery_257.py`;
- conflict-marker scan;
- clean validation worktree;
- final HEAD recheck matched the validated SHA.

PR #270 was marked ready only after that evidence and merged with GitHub's expected-head guard against the exact validated SHA.

## Production acceptance gate
Deploy current canonical `main` to the existing Discloud SniperPlug app.

The first claimed Walmart exact batch must contain:
- `price_recovery=exact_id_search_v257`

That proves the recovery-capable runtime is actually deployed.

Then inspect the paired exact summary:
- success should produce real verified/fresh/reference counts when Walmart supplies usable proof;
- failed recovery must now expose the actual secondary stage, e.g. `api_price_missing->api_exact_search_no_match`, `api_price_missing->api_price_missing`, `api_price_missing->candidate_build`, or a status-derived reason;
- actionable pressure should drain normally;
- any genuine markdown must still pass canonical exact proof and the existing fanout path.

Only after that production evidence identifies a remaining failing stage may implementation change again. Fix the proven stage, not a guessed neighboring layer.

## Blockers / risks
- ChatGPT has repository access but no Discloud runtime/deployment connector, so deployment and live-log acceptance remain external.
- Walmart Affiliate payload shape can change independently; the new telemetry exists to identify that layer before another code change.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's explicit FORCE SWITCH.

Preserved state:
- #265, #266, #267 merged and validated;
- #268 validated and merged at `e970b2dcb5854da06c25c9a9e9b42476975584fa`;
- PR #271 merged Gofobo truthfulness at `60cc3f8b55d2543cfa396105f189ec2427462ca7`;
- PR #272 merged movie result embed-budget protection at `fe0558ea7795d773e0f17e52f8284153711a3b36`;
- remaining movie step is production acceptance on the existing July 31 panel;
- movie work must resume from that acceptance state, not from reimplementation.

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
Deploy current `main`, then judge the first post-deploy Walmart exact batches using the `price_recovery=exact_id_search_v257` marker plus combined recovery-reason telemetry. Do not change Walmart implementation again until that production evidence identifies the actual remaining stage, if any.
