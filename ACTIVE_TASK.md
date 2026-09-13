# Active Task

## Active task / outcome
**SP-WM-EXACT-RECOVERY-DEBT — PR #290 `Serialize Walmart page pacing debt`.**

Outcome: remove the artificial saturated `page_retry` pressure floor caused by local `page_pacing_deferred` waits maturing together, without weakening exact proof or increasing Walmart HTML cadence.

Current canonical main: `9358605543eab5a9d45faedba0f1d09c946cf6b4` (PR #289 merge).
Current branch: `fix/walmart-page-pacing-debt-290`.
Current PR: #290, open draft and mergeable.

Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

## Status
**Implementation and focused cleanup are complete; executable exact-head validation is blocked by GitHub runner non-execution. Do not mark ready or merge yet.**

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
- `claim_due_rows_batched()` and existing page-recovery claim caps are unchanged.
- Existing queue compatibility code already uses the same `UPDATE ... FROM ... RETURNING` SQLite/libSQL shape.

## Scope
Exactly four net changed files:
- `ACTIVE_TASK.md`
- `sniperplug/services/walmart_exact_queue_bulk_runtime.py`
- `sniperplug/services/walmart_page_pacing_debt.py`
- `tests/test_walmart_page_pacing_debt_290.py`

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

## Validation / results
Repository comparison against canonical main confirms the PR is based directly on current main, behind by zero, mergeable, and the net scope is exactly the four intended files.

Focused SQL-shape validation using local SQLite 3.46 passed for:
- a 200-row bounded legacy `WITH ... UPDATE ... FROM ... RETURNING` migration;
- three newly deferred rows serialized to the expected 30/31/32-minute schedule.

Base main was previously validated for PR #289 with a full `1453 passed, 1 warning` suite plus compile/import/pip/native-libsql/diff/scope/conflict/worktree gates. That is base evidence only and is not counted as exact-head #290 validation.

### GitHub-hosted CI blocker
On the latest code-equivalent PR heads, all three hosted workflows are created and then fail before any repository step executes. The inspected `Python Check` job reports `steps=null` and `logs_url=null`. A direct rerun request succeeded, but the replacement job immediately failed with the same no-steps/no-logs state.

Therefore the red checks are runner-side non-execution, not evidence of test failures, but they also cannot be counted as passes.

## Validation still required
Before PR #290 can be called complete/ready:
- v290 regressions;
- #240/#241 page-lane tests;
- #255 page request pacing;
- #256 pacing/claim behavior where present;
- #277-#289 Walmart recovery/dormancy/pressure/Product Lookup/page lifecycle regressions;
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
- exact final SHA recheck.

## Cleanup / conflicts
- No temporary bypass, retry shim, monkey patch, or duplicate proof path was added.
- The canonical exact fetch and bulk persistence paths remain authoritative.
- Unrelated work is untouched.
- GitHub currently reports the PR mergeable against unchanged main.

## Blockers / risks
Only demonstrated blocker: no executable exact-head CI environment is currently producing test evidence. GitHub-hosted jobs fail before runner steps, and this chat does not have the repository's authenticated Python 3.12/native-libsql validation checkout mounted locally.

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
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Obtain executable exact-head validation. If and only if the complete gate passes, record the validated SHA, mark PR #290 ready for review, and proceed to merge/production acceptance separately.
