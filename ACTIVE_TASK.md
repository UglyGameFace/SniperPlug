# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: the canonical SniperPlug production process must run the merged #257 exact-price/reference recovery path, convert eligible Walmart discoveries into exact-verified cards again, drain retry pressure normally, and deliver qualifying deals through the existing public/DM fanout rules. No new Walmart patch is allowed until the deployed execution path proves the merged recovery code itself still fails.

## Scope
`deployed-version proof → canonical Walmart catalog discovery → exact queue claim → fresh-proof/API detail → exact-ID Affiliate-search recovery → selected-offer/current-price/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**Production evidence supplied Sep 8 does not yet prove #257 failed. It proves production is still running a process that started before #257 existed. The immediate defect is stale deployment, not a newly demonstrated code regression.**

Active branch: `fix/walmart-production-acceptance-270`
Base/current main at switch: `e970b2dcb5854da06c25c9a9e9b42476975584fa`
#257 merge: `4d7646dd0b4d120c4c994d60b3d78b6c08760c53`
#257 validated head: `edff57a27fb2e7d342970d0bf2deed782dbbffca`

## Findings / root cause
### Production process is older than #257
The supplied production log has one startup sequence beginning around:
- `2026-09-07 06:30:29` runtime/login/startup
- `2026-09-07 06:30:43` `SniperPlug online`

The same process later emitted the Sep 8 Walmart failures. There is no later startup sequence in the supplied log window.

PR #257 was created later and merged at `2026-09-07T19:41:50Z`. Therefore the running process shown in the logs cannot contain #257's recovery code. This remains true regardless of the one-hour difference between Discloud's prefix timestamp and the application's formatted timestamp.

### Current main already contains #257
Current `main` is `e970b2dcb5854da06c25c9a9e9b42476975584fa` (merge of #268). Comparing #257's merge commit to current main shows main is **128 commits ahead, 0 behind**, and every changed file after #257 is movie-ticket/task work. No Walmart implementation file changed after #257.

So the Walmart implementation on current main is still the validated #257 implementation, including:
- `api_price_missing` recovery through exact-ID Walmart Affiliate search;
- same-cycle trusted-reference enrichment for strict fresh-snapshot candidates;
- HTML disabled for that recovery pass;
- exact item/seller/offer/variant/shipping proof remaining fail-closed.

### What the stale process is doing
The Sep 8 live process repeatedly reports:
- `verified 0`;
- API failures dominating the batch (`page 0`, `api 8/24`);
- `retry_kind=api_price_missing` across whole batches;
- actionable pressure around `117-121` rows;
- manual pass: 398 products across 16 searches, 0 exact-verified markdown cards.

Those symptoms match the pre-#257 failure mode that #257 was written to address. They are not valid post-#257 production acceptance because the process predates the merge.

## Authoritative execution path inspected
Production Walmart exact work uses:
1. `sniperplug/services/walmart_exact_queue_bulk_runtime.py`
2. `fetch_exact_candidate_with_page_pacing()` in `walmart_exact_fetch_pacing.py`
3. `_fetch_exact_candidate_off_loop()` in `walmart_exact_queue_runtime.py`
4. `WalmartPageRecoveryProvider._official_exact_search_payload()`
5. `WalmartProvider.fetch_exact_search_item_payload()` / exact-ID Affiliate search
6. the canonical Walmart candidate builder and selected-offer normalization
7. exact queue persistence/fanout.

#257 specifically intercepts `api_price_missing` after the canonical first pass and gives exact-ID Affiliate search one recovery attempt through the same verifier. No separate proof engine or fallback price guess was added.

## Changes in this active branch
- Switched the repository task record from movie-ticket recovery to this Walmart production-acceptance task.
- Preserved the movie-ticket task below as suspended state.
- **No Walmart runtime code has been changed.** A code edit before a fresh deployment would be guessing against stale production evidence.

## Validation / evidence
### #257 code-side validation already completed
Exact validated head `edff57a27fb2e7d342970d0bf2deed782dbbffca`:
- compileall: pass;
- import smoke: 37 modules / 22 symbols;
- native libsql process tests: 10 passed;
- full suite: 1,286 passed;
- one unrelated `audioop` deprecation warning.

### Current main relationship
- current main: `e970b2dcb5854da06c25c9a9e9b42476975584fa`;
- main contains #257 and is 0 commits behind it;
- no Walmart implementation files changed after #257.

### Current hosted CI state
Current-main GitHub-hosted workflows still fail before execution. The Python Check job for `e970b2d...` has `runner_id=0`, blank runner name, and `steps=[]`; this is the same hosted-runner infrastructure/quota failure seen previously, not an executed test failure.

## Production acceptance gate
The canonical SniperPlug Discloud app must be redeployed from **current `main`**, not from the old running process.

The first post-deploy log must show a fresh startup sequence after this task switch, including `Database connected`, runtime services ready, slash sync, and `SniperPlug online`. Then Walmart acceptance requires:
1. exact batches are demonstrably from the new process;
2. `api_price_missing` whole-batch failures materially collapse or disappear;
3. exact-ID recovery produces verified candidates when Walmart returns usable price data;
4. trusted Walmart references produce `official was prices > 0` when actually supplied;
5. no-reference results remain verified-but-non-markdown rather than becoming false deals;
6. seller/offer/item/variant/shipping guards remain fail-closed;
7. actionable pressure drains instead of staying pinned by the old failure mode;
8. a genuinely qualifying exact markdown reaches canonical fanout/delivery.

If the newly deployed process still returns whole batches of `api_price_missing`, only then is #257 production acceptance failed. The next investigation must capture which recovery stage failed: exact-search request/no-match, returned price shape, candidate extraction, selected-offer normalization, or merge/proof gating. Do not add another fallback until that stage is identified.

## Cleanup / conflicts
- No competing Walmart implementation was created.
- No proof gate, threshold, fanout rule, queue cadence, page circuit, or provider retry rule was changed.
- No temporary/debug code exists in this branch.
- PR #200 eBay watcher remains unrelated and untouched.

## Blockers / risks
- ChatGPT has GitHub access but no connected Discloud deployment integration, and the available plugin directory has no Discloud connector. The production redeploy itself must occur through the existing Discloud GitHub deployment flow or the user's normal Discloud deployment method.
- Walmart's Affiliate payload can change independently. If the fresh deployment still fails, payload-shape evidence must drive the fix rather than assumptions.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's explicit FORCE SWITCH on Sep 8.

Preserved state:
- #265, #266, #267 merged and validated;
- #268 exact validated head: `9a52996ad5dae0c5ccd74e7238dc0fa8cd4f3e80`;
- #268 full validation: targeted pass, pip check pass, compileall pass, import smoke 37/22, native libsql 10 passed, full pytest **1,375 passed**, diff/conflict/worktree checks clean, live DuckDuckGo + combined indexed fallback passed;
- #268 merge: `e970b2dcb5854da06c25c9a9e9b42476975584fa`;
- remaining movie step: deploy current main and run production acceptance on the existing July 31 panel, especially Atom `Indexed fallback current` while direct Atom may remain HTTP 403;
- movie task is not complete and must resume from production acceptance, not from reimplementation.

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
Redeploy canonical SniperPlug from current `main` (`e970b2dcb5854da06c25c9a9e9b42476975584fa`) and collect the fresh startup plus the first Walmart catalog/exact batches. Judge #257 only from that new process. If `api_price_missing` persists there, trace the exact-search recovery payload/normalization path before editing code.