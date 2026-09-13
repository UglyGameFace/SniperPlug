# Active Task

## Active task / outcome
**SP-WM-EXACT-RECOVERY-DEBT — PR #290 `Serialize Walmart page pacing debt`.**

Outcome: remove the artificial saturated `page_retry` pressure floor caused by local `page_pacing_deferred` waits maturing together, without weakening exact proof or increasing Walmart HTML cadence.

Current canonical main: `9358605543eab5a9d45faedba0f1d09c946cf6b4` (PR #289 merge).
Current branch: `fix/walmart-page-pacing-debt-290`.
Current PR: #290, open draft and mergeable.

Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

## Status
**Implementation and focused cleanup are complete; exact-head validation exposed and repaired two test-integration regressions. A fresh complete exact-head rerun is required. Do not mark ready or merge yet.**

## Findings / root cause
PR #288 production telemetry repeatedly showed:

`marker=page_recovery_reason_sample_v288 sampled=64 cap=64 sample_saturated=true reasons=page_pacing_deferred=64`

The exact path is:
1. Fresh/official API proof runs before HTML.
2. Only one HTML-capable fallback may reserve the 60-second process-local probe slot.
3. Other claims that still need HTML return `status=page_retry` with `retry_kind=page_pacing_deferred`.
4. The generic page failure policy gives those local scheduling waits the same 30-minute delay.
5. They mature together and inflate due page pressure even though they are waiting on a local pacing resource rather than an upstream Walmart recovery condition.
6. Existing claim logic refuses to lease old page retries while the probe is paced, but pressure is measured before that claim decision, so the herd is already counted.

This is a scheduling/persistence mismatch. Raising concurrency/caps, weakening proof, changing Product Lookup semantics, or treating Discord gateway latency as the cause would be incorrect.

## Execution path inspected
- `process_actionable_walmart_exact_queue_batch()` initializes runtime schema before v290 normalization, so the status/due index exists before the helper forces that access path.
- `fetch_exact_candidate_with_page_pacing()` remains the canonical fetch path and retains the one-HTML-probe-per-60-seconds boundary.
- `persist_exact_queue_outcomes_bulk()` remains the canonical outcome writer and applies the existing page failure policy before v290 adjusts only local pacing waits.
- Canonical failure persistence writes `last_attempt_at = observed_at`; v290's post-persistence scheduler guards on that exact timestamp before changing the pacing slot.
- `claim_due_rows_batched()` and existing page-recovery claim caps are unchanged.
- Existing queue compatibility code already uses the same `UPDATE ... FROM ... RETURNING` SQLite/libSQL shape.

## Scope
Exactly six net changed files:
- `ACTIVE_TASK.md`
- `sniperplug/services/walmart_exact_queue_bulk_runtime.py`
- `sniperplug/services/walmart_page_pacing_debt.py`
- `tests/test_walmart_page_pacing_debt_290.py`
- `tests/test_autoscan_coverage_stall_253.py`
- `tests/test_walmart_deal_delivery_stall_256.py`

The two existing regression files are included only because they exercise the same exact batch entry point with intentionally non-executable fake DB objects. They now stub the newly added v290 pre-pressure normalization step just as they already stub the other DB-touching collaborators. No production escape hatch was added for fake test connections.

No PR #273, PR #200, setup, DM, HP/Target, site, or other unrelated work is included.

## Changes
- Added durable markers `page_pacing_debt_v290` and `page_pacing_slot_v290`.
- Normalize retained, unleased pre-v290 `page_pacing_deferred` rows before pressure measurement.
- Hard-cap every legacy pass at 200 rows even if a caller supplies a larger limit.
- Preserve each legacy row's existing not-before timestamp; malformed timestamps fail closed instead of being guessed earlier.
- Guard legacy updates against changed `next_attempt_at`, active leases, changed status, and already-slotted rows; count actual returned updates.
- After canonical bulk persistence, serialize only newly written pacing deferrals after the existing durable pacing tail.
- Preserve the existing 30-minute floor for new deferrals and spread later rows one minute apart.
- Keep real page HTTP/challenge/price-mismatch errors, Product Lookup behavior, proof gates, page concurrency, drain concurrency, and claim caps unchanged.
- Add write telemetry: `page_pacing_slots=<n> page_pacing_schedule=page_pacing_slot_v290`.
- Corrected the v290 runtime-wiring regression to assert the imported shared marker constant rather than demand a duplicated literal marker string.
- Updated #253/#256 compatibility tests to stub the new normalization collaborator when they intentionally use `FakeDatabase(object())`.

## Regression coverage added
`tests/test_walmart_page_pacing_debt_290.py` covers:
- 30-minute floor for new pacing deferrals;
- one-minute serialization of simultaneous deferrals;
- new rows appended after an existing pacing tail;
- legacy rows appended after an existing pacing tail;
- real page errors left untouched;
- future legacy not-before values never shortened;
- active leases skipped;
- fixed 200-row migration cap even with an oversized caller limit;
- nonpositive limits perform no migration;
- normalization before pressure measurement;
- new serialization after canonical persistence and before commit.

Existing #253 and #256 tests continue to verify page-circuit claim caps and paced-probe behavior while treating the v290 normalization DB read as an independently stubbed collaborator.

## Validation / results
Repository comparison against canonical main previously confirmed the PR was based directly on current main, behind by zero, and mergeable. Recheck is required on the latest PR head after the compatibility-test and task-record updates.

Focused SQL-shape validation using local SQLite 3.46 passed for:
- a 200-row bounded legacy `WITH ... UPDATE ... FROM ... RETURNING` migration;
- three newly deferred rows serialized to the expected 30/31/32-minute schedule.

On prior exact head `338c75ffe70e65d9579969e761ff5c89f208a406`:
- v290 targeted suite passed: `7 passed, 1 warning`;
- native libsql/process tests, compileall, import smoke, and pip check completed before the full-suite failure, but they are prior-head evidence only after the new test-only commits;
- full suite reached `1458 passed, 2 failed, 1 warning`;
- both failures were existing compatibility tests whose fake DB object did not stub the newly added `normalize_legacy_page_pacing_debt()` call;
- no production runtime defect was indicated by those failures;
- both tests were updated narrowly to stub that collaborator, preserving their original assertions.

The sole warning remains the existing `discord.player` `audioop` deprecation warning for Python 3.13 removal.

### GitHub-hosted CI blocker
On code-equivalent PR heads, all three hosted workflows are created and then fail before any repository step executes. Inspected jobs report `steps=null` and `logs_url=null`; a direct rerun reproduced the same no-steps/no-logs state.

Therefore the hosted red checks are runner-side non-execution, not evidence of test failures, but they also cannot be counted as passes.

## Validation still required
Before PR #290 can be called complete/ready, rerun on the latest live PR head after this task-record update:
- v290 regressions;
- #240/#241 page-lane tests;
- #253 coverage-stall page-circuit regression;
- #255 page request pacing;
- #256 pacing/claim behavior;
- #277-#289 Walmart recovery/dormancy/pressure/Product Lookup/page lifecycle regressions;
- native libsql/process tests;
- gateway #284 regression;
- compileall;
- import smoke;
- pip check;
- full pytest suite;
- real diff check;
- exact six-file changed scope;
- conflict-marker scan;
- clean worktree;
- exact final SHA recheck.

## Cleanup / conflicts
- No temporary bypass, retry shim, runtime fake-connection guard, monkey patch, or duplicate proof path was added.
- The canonical exact fetch and bulk persistence paths remain authoritative.
- The only extra files added to scope are the two directly affected existing regression tests.
- Unrelated work is untouched.

## Blockers / risks
No code blocker is currently known after the two compatibility-test repairs. The remaining blocker is fresh executable exact-head validation. GitHub-hosted jobs still fail before runner steps, so local Python 3.12/native-libsql validation remains the authoritative executable gate.

## Production acceptance after merge
Use fresh deployment logs only. Acceptance requires:
- `marker=page_pacing_debt_v290` when legacy migration occurs;
- `page_pacing_slots=<n> page_pacing_schedule=page_pacing_slot_v290` for newly serialized waits when applicable;
- the v288 sampler stops showing a persistent saturated `page_pacing_deferred=64` floor after migration takes effect;
- HTML remains bounded to one probe per minute;
- no increase in challenge/403/429 failures;
- official exact verification and deal fanout continue normally.

## Preserved prior state
- PR #284, #285, #286, #287, and #288 remain production-accepted for their established purposes.
- PR #289 is merged but still requires a fresh post-deployment code-6001 event before production acceptance is claimed.

## Suspended task
**SP-MOVIE-TICKETS-RECOVERY** remains suspended. Preserve PR #273 unchanged.

## Backlog
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-DM-PERSONALIZATION — category + product type + brand/set + include/exclude + product-aware emojis/deal-strength presentation
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Run one complete executable exact-head validation on the latest PR #290 head after this bookkeeping commit. If and only if the complete gate passes, record the validated SHA in the PR discussion without modifying the branch, mark PR #290 ready for review, and proceed to merge/production acceptance separately.
