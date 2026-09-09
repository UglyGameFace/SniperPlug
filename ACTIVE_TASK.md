# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: production must prove which Walmart exact-recovery build is running, expose the exact stage behind any remaining `api_price_missing` failures, recover exact current/reference prices when Walmart supplies valid same-item data, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue claim → fresh proof/API detail → exact-ID Affiliate-search recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**Fresh production restart boundary is now proven, but the supplied startup alone cannot distinguish #256 from #257 and contains no post-restart Walmart exact batch. PR #270 now adds bounded recovery diagnostics so the next deployed batch is decisive instead of another guessing exercise.**

Active branch: `fix/walmart-production-acceptance-270`
Base main: `e970b2dcb5854da06c25c9a9e9b42476975584fa`
Current PR head before final validation: `78bb472a72636af7bda1da3dd51f731e7b37d252`
#257 merge: `4d7646dd0b4d120c4c994d60b3d78b6c08760c53`
#257 validated head: `edff57a27fb2e7d342970d0bf2deed782dbbffca`

## Findings / root cause
### 1. The newly supplied startup is a real process boundary
The user supplied a later startup beginning at application timestamp `2026-09-08 23:45:22`, with:
- Turso ready / DB connected at `23:45:27`;
- runtime services ready at `23:45:31`;
- slash sync at `23:45:32`;
- `SniperPlug online` at `23:45:34`;
- eligible Walmart fanout guilds at `23:45:35`.

The previously supplied failing Walmart batches at approximately `23:14-23:15` occurred **before** this startup. They cannot be used to judge the restarted process.

### 2. Startup text still does not prove #257 is present
The startup includes `maintenance_after_all_fanout=true`, but that architecture string already existed on the validated #256 head. PR #257 changed only:
- `ACTIVE_TASK.md`;
- `sniperplug/services/walmart_exact_fetch_pacing.py`;
- `tests/test_walmart_exact_price_recovery_257.py`.

#257 did not add a unique startup line. Therefore a restart timestamp after #257's merge is not sufficient proof that Discloud actually deployed that source rather than restarting an older artifact.

### 3. Existing #257 failure reporting hides the secondary recovery result
The authoritative production path is:
1. `walmart_exact_queue_bulk_runtime.py`
2. `fetch_exact_candidate_with_page_pacing()`
3. `_fetch_exact_candidate_off_loop()`
4. official exact detail
5. on `api_price_missing`, #257 exact-ID Affiliate-search recovery
6. canonical candidate/selected-offer normalization
7. bulk persistence and fanout.

Inspection found a concrete observability defect in #257: when the primary pass returns `api_price_missing`, the exact-ID recovery is attempted, but if that recovery also fails, `_recover_api_price_or_reference()` discards the recovery error and returns only the original `api_price_missing`. Production telemetry therefore cannot tell whether recovery failed because of exact-search no-match, another missing-price payload, candidate construction, identity proof, or source unavailability.

That means repeated `api_price_missing` logs are ambiguous even on a post-#257 build. Adding another fallback before exposing the secondary result would be blind patching.

## Changes in PR #270
### Recovery diagnostics only
`sniperplug/services/walmart_exact_fetch_pacing.py`
- successful exact-ID recovery behavior is unchanged;
- when `api_price_missing` recovery fails, preserve the original retry status/error semantics and append only `recovery_kind=<secondary outcome>`;
- if the secondary path has no retry-kind token, report its status as `status_<status>`.

`sniperplug/services/walmart_exact_queue_bulk_runtime.py`
- parse the secondary `recovery_kind` token without changing queue policy;
- report failed batches as, for example, `api_price_missing->api_exact_search_no_match` instead of collapsing everything to `api_price_missing`;
- add `price_recovery=exact_id_search_v257` to the existing write-telemetry line so a production batch itself proves the relevant recovery-capable runtime is deployed.

`tests/test_walmart_exact_price_recovery_257.py`
- preserve all original #257 success/reference tests;
- add a regression proving an exact-search no-match keeps the primary retry semantics while exposing the secondary failure;
- cover the combined retry label and deployed capability marker.

### Explicitly unchanged
- no current/reference price proof is weakened;
- no item, seller, offer, variant, marketplace, condition, fulfillment, or shipping guard is changed;
- no retry status or retry delay is changed;
- no page circuit, HTML pacing, queue cadence, threshold, category, dedupe, fanout, or DM rule is changed;
- no second verifier or guessed/catalog fallback price is added.

## Validation / results
### Previously validated foundation
#257 exact head `edff57a27fb2e7d342970d0bf2deed782dbbffca`:
- compileall passed;
- import smoke 37 modules / 22 symbols;
- native libsql process tests 10 passed;
- full suite 1,286 passed;
- only unrelated `audioop` deprecation warning.

### PR #270 repository checks performed
- base `e970b2d...` → head `78bb472...`: 4 commits ahead, 0 behind;
- exactly 4 changed files: task record, two Walmart recovery/runtime files, one focused test file;
- runtime diff is bounded observability only: 4 additions/1 deletion in pacing and 23 additions/2 deletions in bulk runtime;
- GitHub-hosted workflows again terminated without execution; sampled Python Check job has `steps=null`, so those red statuses are not test results.

### Still required before completion
Exact-head executable validation has **not** yet run for PR #270. Required:
- targeted `tests/test_walmart_exact_price_recovery_257.py` plus relevant Walmart exact/bulk/page-pacing regressions;
- `pip check`;
- compileall;
- import smoke;
- native libsql process suite when available;
- full pytest;
- diff check / conflict scan / clean worktree.

Do not call PR #270 ready or the Walmart task fixed until those checks and production acceptance pass.

## Production acceptance gate after PR #270 validation + merge
Deploy the merged canonical `main`. The first claimed Walmart batch must contain:
- `price_recovery=exact_id_search_v257` in write telemetry, proving the recovery-capable runtime is actually deployed.

Then inspect the paired exact summary:
- success should produce real verified/fresh/reference counts when Walmart supplies usable proof;
- failed price recovery should now expose a combined reason such as `api_price_missing->api_exact_search_no_match`, `api_price_missing->api_price_missing`, `api_price_missing->candidate_build`, or a status-derived recovery reason;
- actionable pressure should drain normally rather than spin indefinitely;
- any genuine markdown must still pass the canonical exact proof and fanout path.

Only after that evidence identifies a remaining failing stage may implementation change again. Fix that stage, not a guessed neighboring layer.

## Cleanup / conflicts
- No competing Walmart implementation exists in this branch.
- No proof/scheduling/fanout behavior changed.
- No temporary bypass, page workaround, retry stack, or duplicate verifier was added.
- PR #200 and watcher work remain unrelated and untouched.

## Blockers / risks
- ChatGPT has repository access but no Discloud runtime/deployment connector, so hosted deployment and live logs remain an external acceptance boundary.
- GitHub-hosted Actions are currently failing before runner execution, so exact-head executable validation must use the known-good external Linux/PRoot environment unless runner availability returns.
- Walmart Affiliate payload shape can change independently; the new telemetry is specifically intended to identify that layer before code changes.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's explicit FORCE SWITCH.

Preserved state:
- #265, #266, #267 merged and validated;
- #268 exact validated head `9a52996ad5dae0c5ccd74e7238dc0fa8cd4f3e80`;
- #268 targeted/pip/compile/import/native-libsql/full validation passed, full pytest **1,375 passed**;
- #268 merge `e970b2dcb5854da06c25c9a9e9b42476975584fa`;
- remaining movie step is production acceptance on the existing July 31 panel, especially Atom indexed fallback health;
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
Run exact-head PR #270 validation. If green, update this record and PR body with the exact validated SHA/results, mark ready, merge only that SHA, deploy merged main, then judge the first Walmart exact batches using the new `price_recovery` marker and combined recovery reason telemetry.