# Active Task

## Status
**SP-WM-EXACT-RECOVERY-DEBT — IN PROGRESS.**

The active lock remains Walmart exact-recovery debt until the remaining queue/recovery failure classes are resolved from production evidence. Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

Current base main: `9358605543eab5a9d45faedba0f1d09c946cf6b4` (PR #289 merge).

Current focused branch: `fix/walmart-page-pacing-debt-290`.
Current focused PR: #290, not opened yet.

## Completed prerequisite — SP-GATEWAY-EVENT-LOOP-LAG
PR #284 remains production-accepted.

The supplied window again shows ~41-second Discord heartbeat latency while local loop lag remains low (`recent_loop_lag_max_s` 0.17-0.41s around the warnings), classification remains `gateway_ack_latency_loop_healthy`, and latency recovers normally. Do not reopen or throttle Walmart based solely on these Discord ACK/network-path events.

## PR #285 production acceptance — confirmed no-price debt
PR #285 remains production-accepted.

Mature dual `api_price_missing -> api_price_missing` rows continue to be parked with `marker=confirmed_no_price_dormancy_v285`. The latest supplied window shows repeated bounded parking batches while exact verification continues to produce valid verified offers. Fresh/material catalog evidence can still wake parked rows immediately.

## PR #286 / #287 production acceptance
PR #286 remains production-accepted for readable Product Lookup error bodies.

PR #287 remains production-accepted for deterministic page HTTPError closure and HTTP 410 page-absence handling. The persistent page backlog was not caused primarily by 410s.

## PR #288 production acceptance — page-recovery reason sample
PR #288 is now production-accepted for its diagnostic purpose.

Fresh production logs repeatedly show:

`marker=page_recovery_reason_sample_v288 sampled=64 cap=64 sample_saturated=true reasons=page_pacing_deferred=64`

The same 64/64 distribution appears at 21:05:38, 21:10:40, and again after the 21:15 restart at 21:15:37. This is decisive evidence that the saturated due page-recovery lane is local pacing debt, not a mixed population of Walmart 403/429/challenge/410 failures.

## PR #289 merged — Product Lookup 6001 normalization
PR #289 merged to main as `9358605543eab5a9d45faedba0f1d09c946cf6b4` from validated head `1cd3393cb201f1486e4ed1f3308c215676705e97`.

Exact-head validation passed before merge:
- targeted Walmart/gateway regressions: `486 passed` on the prior code head;
- final exact-head full suite: `1453 passed, 1 warning`;
- native libsql/process environment available under the known Python 3.12 validation venv;
- compile/import/pip checks passed;
- real `git diff origin/main...HEAD --check` passed;
- exact three-file scope passed;
- conflict-marker scan passed;
- clean worktree passed;
- final SHA matched exactly.

### PR #289 production acceptance still pending
The supplied code-6001 line at 21:05:34 still used the pre-#289 generic diagnostic/retry semantics and occurred before the 21:15 process restart. The post-restart window supplied so far contains no new code-6001 event. Do not claim #289 production acceptance until a fresh post-deployment 6001 response emits `marker=lookup_6001_no_match_v289` and surfaces as `api_exact_lookup_no_match` / `api_price_missing->api_exact_lookup_no_match` while unrelated HTTP 400s remain generic.

## Current root cause — durable page pacing debt
The exact execution path is now proven:

1. Every claim first gets fresh/official API exact verification with HTML suppressed.
2. If canonical seller/offer identity still needs the product-page fallback, only one claim per process may reserve the 60-second HTML probe slot.
3. Other claims that finish API work while that slot is occupied return `retry_kind=page_pacing_deferred` with status `page_retry`.
4. `_failure_storage_policy` treats that local scheduling wait like the default page failure and assigns the generic 30-minute page retry delay.
5. Multiple rows therefore mature together after the same coarse delay. Once mature they count as due `page_retry` pressure even though they are waiting on a local one-probe-per-minute resource, not on Walmart recovery time.
6. The bulk worker correctly sets page-recovery claim cap to zero while the probe remains paced, which prevents pointless re-leasing, but that happens after the rows are already counted as due pressure. The result is the observed 64/64 `page_pacing_deferred` saturation.

This is a scheduling/persistence mismatch. Raising page concurrency, raising claim caps, weakening proof, changing the Product Lookup request, or treating the Discord gateway warning as the cause would all be the wrong fix.

## Implementation scope — PR #290
Branch: `fix/walmart-page-pacing-debt-290`, based directly on PR #289 merge main `9358605543eab5a9d45faedba0f1d09c946cf6b4`.

The intended fix is deliberately narrow:

1. Add a durable page-pacing scheduler with markers `page_pacing_debt_v290` and `page_pacing_slot_v290`.
2. Before queue pressure is measured, migrate retained, unleased pre-v290 `page_pacing_deferred` rows in bounded batches of at most 200.
3. Preserve every row's existing not-before timestamp; never make a legacy row eligible earlier than its current schedule.
4. Serialize those rows after any existing scheduled pacing tail at one slot per 60 seconds, matching the existing HTML probe guard.
5. After canonical bulk persistence, detect only newly written `page_pacing_deferred` outcomes and serialize them into durable one-minute slots.
6. Preserve the existing 30-minute page-retry floor for newly deferred rows. The first new pacing slot cannot be earlier than that existing floor; later rows are spread one minute apart.
7. Do not touch real `page_http_403`, `page_http_429`, `page_challenge`, `page_price_mismatch`, 410/page-absence behavior, exact proof gates, seller/offer/variant/marketplace/shipping validation, Product Lookup semantics, page probe concurrency, drain concurrency, or claim caps.
8. Emit bounded telemetry for legacy normalization and per-batch scheduled pacing rows so production acceptance is measurable.

## Current #290 changed files
Intended net scope is four files:
- `ACTIVE_TASK.md`;
- `sniperplug/services/walmart_page_pacing_debt.py` (new);
- `sniperplug/services/walmart_exact_queue_bulk_runtime.py`;
- `tests/test_walmart_page_pacing_debt_290.py` (new).

## Validation required for PR #290
- regression proving newly deferred rows keep the existing 30-minute not-before floor;
- regression proving simultaneous pacing deferrals are serialized one minute apart;
- regression proving an existing serialized pacing tail is respected;
- regression proving legacy pacing rows are migrated in a bounded batch;
- regression proving unrelated real page errors are untouched;
- regression proving normalization occurs before pressure measurement;
- regression proving new pacing serialization occurs after canonical persistence and before commit;
- existing #240/#241 page-lane gates;
- #255 page-request pacing tests;
- #256 pacing/claim behavior tests where present;
- #277-#289 exact recovery, dormancy, queue pressure, Product Lookup, and page lifecycle regressions;
- native libsql/process tests;
- gateway #284 regression;
- compileall;
- import smoke;
- pip check;
- full pytest suite;
- `git diff --check`;
- exact changed-file scope;
- conflict-marker scan;
- clean worktree;
- final exact SHA recheck.

## Production acceptance for PR #290
After merge/deployment, use fresh logs only. Acceptance requires:
- `marker=page_pacing_debt_v290` for any legacy migration work;
- write telemetry may show `page_pacing_slots=<n> page_pacing_schedule=page_pacing_slot_v290` when new local pacing waits are serialized;
- `page_recovery_reason_sample_v288` must stop showing a persistent saturated `page_pacing_deferred=64` floor after the migrated schedule takes effect;
- HTML remains bounded to the existing one-probe-per-minute guard;
- no increase in challenge/403/429 failures;
- official exact verification and fanout continue normally.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's earlier FORCE SWITCH. Preserve PR #273 and all merged movie-ticket work unchanged.

## Backlog — not active
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Review the exact #290 diff, open it as a draft, and obtain executable exact-head validation. Do not merge until the complete gate above passes. Separately, wait for a fresh post-#289 code-6001 production event before calling #289 production-accepted.
