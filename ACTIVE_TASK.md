# Active Task

## Status
**SP-WM-EXACT-RECOVERY-DEBT — IN PROGRESS.**

The active lock remains Walmart exact-recovery debt until the remaining queue/recovery failure classes are resolved from production evidence. Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

Base main for the current follow-up: `98f49ef86374ed359bb3d3c899ba44789c632371` (PR #288 merge).

Current focused branch: `fix/walmart-lookup-6001-normalization-289`.
Current focused PR: #289, draft.

## Completed prerequisite — SP-GATEWAY-EVENT-LOOP-LAG
PR #284 remains production-accepted.

A ~41-second Discord heartbeat warning with local loop lag below one second was classified `gateway_ack_latency_loop_healthy` and recovered normally. Do not reopen or throttle Walmart based solely on Discord ACK/network-path latency while local event-loop telemetry remains healthy.

## PR #285 production acceptance — confirmed no-price debt
PR #285 remains live and working as intended.

Mature dual `api_price_missing -> api_price_missing` rows are parked into the 30-day safety reprobe lane while complete fresh/material catalog evidence can wake them immediately. Production pressure for this lane has fallen from the former saturated population to single digits or zero in observed windows.

## PR #286 production acceptance — readable Product Lookup errors
PR #286 remains production-accepted.

The deployed runtime repeatedly emits `decode=lookup_http_error_decode_v286` with readable Walmart Affiliate Product Lookup HTTP 400 bodies. The important live error is Walmart code `6001` with message `Results not found`.

The current exact request remains `ids=<item_id>` with `responseGroup=full`. Successful exact Product Lookup requests continue in the same runtime, so code 6001 is not evidence that the shared request shape itself is malformed.

## PR #287 production acceptance — HTTP 410 / response lifecycle
PR #287 remains production-accepted for the observed post-merge window.

The page HTTPError lifecycle is deterministic, HTTP 410 Gone is treated like page absence rather than a retryable product-page failure, no new `page_http_410` outcome appeared in the acceptance window, and no matching unclosed SSL ResourceWarning appeared.

The page-recovery due lane nevertheless remained saturated at `>=64`, so 410 was not the whole backlog.

## PR #288 merged — bounded page-recovery reason observability
PR #288 is merged as current main `98f49ef86374ed359bb3d3c899ba44789c632371`.

It adds `marker=page_recovery_reason_sample_v288` without changing queue behavior:
- only when page recovery is saturated at the 64-row pressure cap;
- sample at most 64 due, retained, unleased `page_retry` rows;
- force the read through `idx_walmart_exact_detail_queue_status_due`;
- group stored `retry_kind=` values with `unclassified` fallback;
- rate-limit the extra read to once every five minutes per process;
- fail open if the diagnostic read fails.

Exact-head PR #288 validation passed on `b757cc066654bf7e83f4243d3fa5ec87dd4a6ed6`: targeted tests, native libsql/process tests, compile, import smoke, pip check, full suite `1450 passed, 1 warning`, diff/scope/conflict/worktree checks, and final SHA recheck.

### PR #288 production acceptance still pending
Fresh merged-deployment logs containing `marker=page_recovery_reason_sample_v288` are still required before changing page retry scheduling, pacing, circuit behavior, claim caps, or concurrency.

The batch result's current `page` counter is not a stored backlog histogram. Use the new bounded reason sample to identify the dominant durable `page_retry` cause first.

## Current root cause — Affiliate Product Lookup 6001
Current code already uses `api_exact_lookup_no_match` when a successful Product Lookup payload contains no exact requested item. The remaining inconsistency is transport-level HTTP 400 code 6001 / `Results not found`, which #286 currently exposes as generic `api_http_400`.

Evidence supports a narrow semantic normalization rather than an endpoint change:
- the same legacy Affiliate Product Lookup `ids` route can succeed for other item IDs in the same runtime;
- the live upstream body explicitly says `Results not found`;
- historical behavior of this legacy route shows code 6001 for item IDs that Product Lookup cannot return while the same request shape works for other IDs;
- therefore this response is an exact lookup no-result condition, not proof of malformed shared request syntax.

## Implementation scope — PR #289
1. Preserve the Affiliate Product Lookup endpoint, `ids=<item_id>`, `responseGroup=full`, signing, timeout, exact item verification, and all canonical proof gates.
2. After #286 decoding, parse the HTTP 400 body as JSON.
3. Normalize only a nonempty `errors` list whose complete error set is exactly code `6001` with message `Results not found` to the existing retry kind `api_exact_lookup_no_match`.
4. Preserve every other HTTP 400, including code `4008` / `Missing itemId`, as generic `api_http_400`.
5. Keep deterministic HTTPError closing and gzip/deflate decoding unchanged.
6. Emit `marker=lookup_6001_no_match_v289` for the normalized production path.
7. Do not change retry delays, dormancy, queue claim priority, page recovery, Product Lookup request shape, current-price proof, reference proof, seller/offer/variant/marketplace/shipping validation, or fanout in this PR.

## Scheduling decision for PR #289
Do not extend #285 dormancy to 6001 in this patch.

The #285 dormancy signature is stronger and different: both official exact price paths returned the exact item but no canonical current price. A 6001 result means the secondary Product Lookup returned no item result. Keep the existing generic retry/backoff and catalog wake behavior until the normalized class is visible in fresh production telemetry. The expected batch reason will become `api_price_missing->api_exact_lookup_no_match`, which can then be measured separately from malformed/validation HTTP 400s.

## PR #289 current changes
The intended net scope remains exactly three files:
- `ACTIVE_TASK.md`;
- `sniperplug/providers/walmart_exact_lookup.py`;
- `tests/test_walmart_exact_lookup_results_not_found_289.py`.

The provider patch performs strict structured normalization only. It does not alter the Product Lookup request, exact proof path, page path, persistence policy, or fanout.

The new regression covers the live gzip-compressed code-6001 body, response closure/readability, the v289 marker, strict mixed/other-error rejection, and preservation of generic code-4008 behavior.

## PR #289 validation status
**PENDING. Do not mark ready or merge.**

GitHub-hosted `Python tests`, `Python Check`, and `Import smoke check` were created for the first #289 head but failed before executing any steps. Their jobs report `steps=[]`, `runner_id=0`, and no assigned runner. Treat those conclusions as runner-side non-evidence, not code-test failures and not successful validation.

No exact-head full-suite completion claim is authorized until executable validation is available.

## Validation required for PR #289
- targeted regression for gzip-compressed HTTP 400 code 6001 / `Results not found`;
- regression proving response closure and readable detail are preserved;
- regression proving mixed/other HTTP 400 bodies are not normalized;
- existing #286 code-4008 generic HTTP 400 regression;
- #274 exact Product Lookup request/no-match regressions;
- #275 diagnostics and #276 handoff regressions;
- surrounding #277-#288 Walmart recovery, queue, dormancy, pressure, page-pacing, page-circuit, and page-lifecycle regressions;
- native libsql/process tests;
- gateway #284 regression where applicable;
- compileall;
- import smoke;
- pip check;
- full pytest suite;
- `git diff --check`;
- exact changed-file scope;
- conflict-marker scan;
- clean worktree;
- final exact SHA recheck.

## Production acceptance for PR #289
After merge and deployment, use fresh logs only.

Acceptance requires:
- `marker=lookup_6001_no_match_v289` on real code-6001 responses;
- those outcomes surface as `api_exact_lookup_no_match` / `api_price_missing->api_exact_lookup_no_match` rather than generic `api_http_400`;
- unrelated HTTP 400 validation failures remain generic;
- successful exact verification and all proof gates remain intact.

Page-recovery behavior remains gated separately on fresh `page_recovery_reason_sample_v288` logs.

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
Obtain executable exact-head validation for draft PR #289. Keep it draft and unmerged until targeted/compatibility tests, native process/libsql tests, compile/import/pip checks, the full suite, diff/scope/conflict/worktree checks, and final SHA recheck all pass. Separately, do not authorize a page-lane behavior change until fresh merged-deployment `page_recovery_reason_sample_v288` telemetry identifies the dominant stored page-retry reason.
