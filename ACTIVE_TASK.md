# Active Task

## Status
**SP-WM-EXACT-RECOVERY-DEBT — IN PROGRESS, implementation complete pending executable exact-head validation.**

The active lock remains Walmart exact-recovery debt until this PR clears its full validation gate and, after merge/deployment, the pacing change is accepted from fresh production evidence. Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

Current canonical main: `9358605543eab5a9d45faedba0f1d09c946cf6b4` (PR #289 merge).

Current focused branch: `fix/walmart-page-pacing-debt-290`.
Current focused PR: #290, open draft and mergeable.
Current focused head after final status bookkeeping: `873b572b539b5a91d514868a80087bd66bab574e`.

## Outcome sought
Remove the artificial saturated `page_retry` pressure floor caused by local `page_pacing_deferred` waits maturing together, without weakening Walmart exact proof or increasing HTML request cadence.

## Completed prerequisite — SP-GATEWAY-EVENT-LOOP-LAG
PR #284 remains production-accepted.

The supplied window again showed ~41-second Discord heartbeat latency while local loop lag remained low (`recent_loop_lag_max_s` 0.17-0.41s around the warnings), classification remained `gateway_ack_latency_loop_healthy`, and latency recovered normally. Do not reopen or throttle Walmart based solely on those Discord ACK/network-path events.

## PR #285 production acceptance — confirmed no-price debt
PR #285 remains production-accepted.

Mature dual `api_price_missing -> api_price_missing` rows continue to be parked with `marker=confirmed_no_price_dormancy_v285`. Complete fresh/material catalog evidence can still wake parked rows immediately.

## PR #286 / #287 production acceptance
PR #286 remains production-accepted for readable Product Lookup error bodies.

PR #287 remains production-accepted for deterministic page HTTPError closure and HTTP 410 page-absence handling. The persistent page backlog was not caused primarily by 410s.

## PR #288 production acceptance — page-recovery reason sample
PR #288 is production-accepted for its diagnostic purpose.

Fresh production logs repeatedly showed:

`marker=page_recovery_reason_sample_v288 sampled=64 cap=64 sample_saturated=true reasons=page_pacing_deferred=64`

The same 64/64 distribution appeared repeatedly, including after restart. This proves the saturated due page-recovery lane is local pacing debt, not a mixed population of Walmart 403/429/challenge/410 failures.

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
Do not claim #289 production acceptance until a fresh post-deployment 6001 response emits `marker=lookup_6001_no_match_v289` and surfaces as `api_exact_lookup_no_match` / `api_price_missing->api_exact_lookup_no_match` while unrelated HTTP 400s remain generic.

## Root cause — durable page pacing debt
The exact execution path is proven:

1. Every claim first gets fresh/official API exact verification with HTML suppressed.
2. If canonical seller/offer identity still needs the product-page fallback, only one claim per process may reserve the 60-second HTML probe slot.
3. Other claims that finish API work while that slot is occupied return `retry_kind=page_pacing_deferred` with `page_retry` status.
4. `_failure_storage_policy` gives those local scheduling waits the generic 30-minute page-retry delay.
5. Multiple rows therefore mature together after the same coarse delay and count as due `page_retry` pressure even though they are waiting on a local one-probe-per-minute resource rather than Walmart recovery time.
6. The worker correctly refuses to lease old page retries while the probe is paced, but that happens only after those rows are already counted as due pressure.

This is a scheduling/persistence mismatch. Raising page concurrency, raising claim caps, weakening proof, changing Product Lookup semantics, or treating Discord gateway latency as the cause would be incorrect.

## Execution path inspected for PR #290
- `process_actionable_walmart_exact_queue_batch()` initializes runtime schema before the new normalization call, so `idx_walmart_exact_detail_queue_status_due` exists in production before the helper forces that access path.
- `fetch_exact_candidate_with_page_pacing()` remains the canonical fetch path and still allows at most one HTML-capable pass per 60 seconds.
- `persist_exact_queue_outcomes_bulk()` remains the canonical outcome writer and still applies the existing page failure policy before v290 adjusts only local pacing waits.
- `claim_due_rows_batched()` and existing page-recovery claim caps remain unchanged.
- Existing `UPDATE ... FROM ... RETURNING` patterns in the queue compatibility code confirm the same SQLite/libSQL SQL shape is already part of the validated codebase.

## PR #290 implementation
Net scope remains exactly four files:
- `ACTIVE_TASK.md`;
- `sniperplug/services/walmart_exact_queue_bulk_runtime.py`;
- `sniperplug/services/walmart_page_pacing_debt.py`;
- `tests/test_walmart_page_pacing_debt_290.py`.

Implemented behavior:
1. Add durable markers `page_pacing_debt_v290` and `page_pacing_slot_v290`.
2. Before pressure measurement, normalize retained, unleased pre-v290 `page_pacing_deferred` rows into durable one-minute slots.
3. Hard-cap every legacy migration pass at 200 rows even if a larger caller limit is supplied, keeping the bulk CTE below conservative SQLite parameter ceilings.
4. Preserve each legacy row's existing not-before timestamp; malformed timestamps fail closed rather than being guessed earlier.
5. Guard legacy updates against a changed `next_attempt_at`, active lease, changed status, or already-slotted error marker and count only rows actually returned by the update.
6. After canonical bulk persistence, serialize newly written `page_pacing_deferred` outcomes after the existing pacing tail.
7. Preserve the existing 30-minute page-retry floor for newly deferred rows, then spread later rows one minute apart.
8. Keep real page HTTP/challenge/price-mismatch errors, Product Lookup semantics, proof gates, page probe concurrency, drain concurrency, and claim caps unchanged.
9. Expose `page_pacing_slots=<n> page_pacing_schedule=page_pacing_slot_v290` in existing write telemetry.

## Regression coverage added
`tests/test_walmart_page_pacing_debt_290.py` now covers:
- new pacing deferrals retain the existing 30-minute floor;
- simultaneous new deferrals are serialized one minute apart;
- new deferrals append after an already serialized pacing tail;
- legacy pacing rows append after the existing tail;
- unrelated real page failures remain untouched;
- a future legacy not-before value is never shortened;
- an actively leased legacy row is not rescheduled;
- a caller cannot expand a migration pass beyond the fixed 200-row cap;
- a nonpositive migration limit performs no work;
- normalization occurs before pressure measurement;
- new pacing serialization occurs after canonical bulk persistence and before commit.

## Validation/results so far
Repository comparison against canonical main confirms the PR is ahead by seven commits, behind by zero, and its net changed-file scope remains exactly the intended four files.

Independent local SQLite 3.46 SQL-shape checks passed for the new CTE/update forms, including:
- a 200-row legacy migration update using the bounded parameter set and `RETURNING item_id`;
- three newly deferred rows serialized to the 30/31/32-minute slots with the same `UPDATE ... FROM ... RETURNING` structure.

The canonical base main itself remains previously validated at `1453 passed, 1 warning` before PR #289 merged. That is base evidence only, not exact-head PR #290 validation.

### Hosted CI is currently non-evidence
On final head `873b572b539b5a91d514868a80087bd66bab574e`, all three GitHub-hosted workflows were created but failed before executing repository steps:
- `Python tests` run 575;
- `Import smoke check` run 1085;
- `Python Check` run 1085.

The inspected `Python Check` job reported no executable steps/logs (`steps=null`, `logs_url=null`). A direct rerun succeeded as an API request but immediately produced a replacement job with the same `steps=null`, `logs_url=null` failure. This confirms the red check is runner-side non-execution rather than a repository test result, but it still cannot be counted as a pass.

## Validation still required before completion/merge
The PR must remain draft until an executable exact-head environment produces the full gate:
- v290 regression file;
- existing #240/#241 page-lane gates;
- #255 page-request pacing tests;
- #256 pacing/claim behavior where present;
- #277-#289 exact recovery, dormancy, queue pressure, Product Lookup, and page lifecycle regressions;
- native libsql/process tests;
- gateway #284 regression;
- compileall;
- import smoke;
- pip check;
- full pytest suite;
- real diff check;
- exact changed-file scope;
- conflict-marker scan;
- clean worktree;
- final exact SHA recheck.

Do not mark the PR ready or merge it based only on the implementation edits or runner-less red checks.

## Cleanup/conflict inspection
- No unrelated implementation was started.
- No page proof, seller/offer/variant/marketplace/shipping gate was duplicated or weakened.
- No temporary retry, fallback, monkey patch, or startup bypass was added.
- The affected runtime still has one canonical exact fetch path and one canonical bulk persistence path.
- Current GitHub comparison reports the PR mergeable against unchanged main.

## Blocker/risk
The only currently demonstrated completion blocker is executable exact-head validation. GitHub-hosted jobs are failing before a runner executes any step, and this chat does not have a mounted authenticated repository checkout capable of substituting for the repo's full Python 3.12/libsql validation environment.

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
Obtain executable exact-head validation for PR #290. If the complete gate passes, refresh this record with the exact validated SHA, mark the PR ready for review, and only then consider merge. Separately, wait for fresh post-#289 code-6001 production evidence before calling PR #289 production-accepted.
