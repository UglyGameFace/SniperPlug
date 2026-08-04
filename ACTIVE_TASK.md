# Active Task

## Status
Active — the production bot is healthy on Python 3.11.15, but live proof showed the exact queue still caused Discord heartbeat stalls. PR #204 is merged with bounded bulk Turso persistence and now needs deployment plus post-deploy lag validation.

## Scope
Restore reliable Walmart background alerts without weakening exact item, seller, offer, current-price, original-price, duplicate, category, or per-server threshold proof.

## Production findings
- The 420 Lobby is correctly enrolled in live fanout with exact guild ID `1514374173517152418`.
- PR #202 fixed the original backpressure arithmetic and is deployed.
- PR #203 added bounded 24/4 drain mode, batch queue leasing, tiered rechecks, and stage timings.
- Replacement app `1785806676351` is live on Python 3.11.15, connected to Turso, logged into Discord, and reports the correct eligible fanout guilds.
- Old app `1779293887444` remains stopped as rollback.
- On Python 3.11, eight sampled drain cycles reduced actionable due from 1,338 to 1,029, a drop of 309 rows.
- Every sampled cycle remained in drain mode at 24 claims / concurrency 4.
- `new/retry due` remained zero; the remaining actionable work was scheduled rechecks.
- Catalog discovery remained correctly paused above the 450-row pressure limit.
- No queue/fanout batch failures were captured in the sample.
- Python 3.11 did not remove the event-loop problem: 40 lag warnings were captured with a maximum of 30.61 seconds.
- Discord logged `heartbeat blocked for more than 10 seconds` during exact queue work.
- The blocked-loop traceback showed Discord attempting to write its heartbeat after the process had already been starved; it confirmed the symptom rather than identifying a Python-level queue frame.
- The exact worker's persistence stage previously issued one queue update plus multiple offer-memory SELECT/INSERT/UPDATE operations per candidate, exceeding roughly 70 serialized Turso/libsql calls in a 24-item cycle.
- One sampled pre-fix store phase reached 22.39 seconds; claim phases reached 19.29 seconds on Python 3.11.

## Completed changes
- Added bounded drain mode: 24 claims with concurrency 4 only while actionable due is at least 450; normal mode remains 6/2.
- Replaced per-row queue leasing with one ordered SELECT, one guarded batch UPDATE, and one verification SELECT.
- Preserved pending/retry priority ahead of scheduled rechecks.
- Kept terminal identity failures excluded and fail-closed.
- Kept 50%+ verified markdowns on a one-hour recheck.
- Changed 30–49% markdown rechecks to six hours and 10–29% markdown rechecks to twelve hours.
- Changed no-reference rechecks to twelve hours and under-threshold/unavailable rechecks to twenty-four hours.
- Split health output into `new/retry due` and `scheduled rechecks due` while preserving total actionable backpressure.
- Added claim, fetch, and store timing to each queue batch log.
- Rebuilt production under the repository-pinned Python 3.11 runtime while preserving a stopped rollback application.
- PR #204 added a separate bulk persistence runtime used only by the global production worker.
- PR #204 consolidates all claimed queue outcomes into one guarded queue UPDATE.
- PR #204 consolidates offer-memory persistence into one SELECT and one guarded UPSERT.
- Exact item, seller, selected offer, variant, condition, fulfillment, trusted-reference, retry, terminal quarantine, and tiered recheck rules remain unchanged.
- The prior runtime remains in the repository for rollback and comparison.

## Validation completed
- Python compilation passed.
- Import smoke check passed.
- Python Check passed.
- Full repository suite passed: 965 tests, 1 unrelated discord.py audioop deprecation warning.
- A 24-item bulk persistence regression proves exactly three SQL statements before commit: one queue UPDATE, one offer-memory SELECT, and one offer-memory UPSERT.
- Failure persistence regression proves retry updates preserve prior exact snapshot fields.
- Offer-memory regression proves learning, stable-reference confirmation, and `new_low` behavior remain intact.
- Existing batch leasing, terminal exclusion, health splitting, drain mode, exact proof, and tiered recheck tests remain passing.
- PR #204 merged as commit `623c83c4d3811b2c1548e718871efd4f712d9cfc`.

## Remaining live definition of done
- Deploy merged commit `623c83c4d3811b2c1548e718871efd4f712d9cfc` to replacement app `1785806676351` without starting rollback app `1779293887444`.
- Confirm startup reports Python 3.11.15 and `bulk_exact_persistence=true`.
- Observe multiple 24/4 drain cycles while actionable due remains above 450.
- Confirm queue and offer-memory persistence continue with no batch failures.
- Compare claim/store timings and event-loop warnings against the pre-fix Python 3.11 maximum of 30.61 seconds.
- Confirm no repeated Discord heartbeat-blocked warnings under exact queue work.
- Confirm exact-deal fanout continues with zero public/DM errors.
- Confirm catalog discovery remains paused above 450 and resumes only below the safe pressure limit.
- Keep the old app stopped until enough post-fix production evidence exists for rollback retirement.

## Blockers
- No code or CI blocker remains.
- Final closure depends on deploying PR #204 and collecting post-deploy production evidence.

## Backlog
- Improve the separate movie-ticket source availability behavior after the Walmart alert task is fully closed.
- Review optional PyNaCl/davey voice dependencies separately; they do not affect Walmart alerts.
- Delete the stopped Python 3.14 rollback app and obsolete deployment branches only after final runtime validation.
