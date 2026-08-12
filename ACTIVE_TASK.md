# Active Task

## Status
**Code-side implementation and Definition-of-Done validation complete on PR #232 (`fix/dm-alert-diversity-unlimited`).** Merge remains gated on GitHub validating the final documentation-only task-record head; do not merge unless all required workflows are green and the branch remains current with `main`.

## Task
**SP-DM-DIVERSITY — Prevent broad Smart DM alerts from being monopolized by apparel/fashion deals and add a true Unlimited daily-limit option**

## Scope
1. Preserve every existing hard personal filter, exact-price proof requirement, dedupe rule, retailer/location guard, favorite, mute, hard allowlist, and strict Flip Override behavior.
2. Stop broad Smart alerts with a finite daily cap from letting one category family consume the user's available DM slots merely because those events arrived first.
3. Treat `premium_apparel`, `shoes_apparel`, and `sneakers` as one fashion family for delivery balancing so aliases cannot bypass the guard.
4. Keep intentional user choices authoritative: hard allowlists, favorite categories, and strict Flip Override opportunities are not suppressed by automatic diversity balancing.
5. Add one shared durable delivery policy used by live Walmart bulk fanout and verified HP/Target event fanout; retain compatible semantics in the legacy Walmart fanout implementation.
6. Add a real Unlimited personal-DM option. Unlimited removes the total daily alert cap rather than encoding a fake large number; it survives normalization/storage and displays clearly in both DM setup surfaces.
7. Make diversity state durable across process restarts rather than relying on in-memory counters.
8. Add focused regression coverage, compile/static validation, full-suite validation, cleanup, and conflict inspection before merge.

## Root cause / findings
- `sniperplug/services/walmart_global_deal_fanout_bulk.py` is the live Walmart scheduled fanout imported by `global_auto_scan_runner.py`.
- Walmart bulk fanout evaluated verified events in queue/FIFO order and immediately sent each matched DM until the subscriber's total daily receipt count reached `max_alerts_per_day`.
- `sniperplug/services/verified_retailer_event_fanout.py` used the same total-count-only behavior for HP/Target verified events.
- No shared delivery-diversity guard existed, so an apparel-heavy verified wave could consume a finite daily cap before later tech/home/gaming/tools events arrived.
- The prior preference model clamped `max_alerts_per_day` to 1–100, the menu rejected a no-cap value, and `_preference_from_row` converted a stored zero back to the default. Unlimited therefore did not exist.
- Existing canonical DM receipts persisted only `user_id`, `deal_key`, and `delivered_at`; category-family counts could not survive a restart.
- The live matcher already knew category keys, favorites, hard allowlists, mutes, and strict flip qualification, so the delivery layer could preserve those decisions instead of re-guessing intent from product titles.

## Changes
- `0` is now the canonical durable Unlimited sentinel while 25/day remains the default for existing users.
- Both the personal DM menu and typed `/dm_deals` setup accept explicit Unlimited input and display `unlimited` instead of exposing the sentinel.
- Unlimited bypasses the total daily cap and automatic family quota; it is not replaced by a hidden second cap.
- Added `sniperplug/services/dm_delivery_policy.py` as the one shared post-match delivery policy.
- `premium_apparel`, `shoes_apparel`, and `sneakers` collapse into one `fashion` family for automatic balancing.
- Broad Smart + finite-cap delivery reserves category variety. At the default 25/day, fashion receives at most 7 automatic slots; other families use the shared larger family-share ceiling.
- Hard allowlists, favorite categories, non-Smart modes, and strict Flip Override opportunities bypass automatic diversity balancing while all pre-existing hard personal filters remain enforced.
- Added a per-user async delivery lock so live retailer fanouts cannot race each other past the same finite cap inside one process.
- Added durable family receipt history in `user_dm_deal_alert_family_receipts` with the same 90-day retention window as canonical DM receipts.
- Successful sends write canonical dedupe and family history in the same transaction; duplicate receipt timestamps remain immutable.
- Live Walmart bulk fanout, verified HP/Target fanout, and retained legacy Walmart fanout all use the same delivery gate/receipt writer/lock instead of carrying retailer-specific cap logic.
- Added a clean `priority_override` match flag for strict Flip Override qualification rather than parsing human-readable matcher reasons.
- DM delivery telemetry and fanout summaries now expose diversity-reserve skips separately from filter rejects, duplicates, and daily-cap skips.
- Explicit DM-data deletion also removes durable family history.

## Validation
- Focused tests cover Unlimited parsing/normalization/display, fashion-family alias grouping, finite family quotas, Unlimited bypass semantics, total-cap precedence, favorite/allowlist/non-Smart/strict-flip exceptions, all retained fanout integrations, telemetry, and an actual in-memory SQLite family-receipt schema/persistence/reinitialization round trip.
- Initial Python 3.11 full-suite run exposed one stale observability assertion that still expected the old `filter/duplicate/cap` summary. Runtime implementation, import, and compilation were already passing; the regression was corrected to require the new diversity bucket instead of weakening production telemetry.
- Corrected PR head `0455945b24c6f71c3454617b9a4a1668f9eec1ad` validation:
  - Import smoke check: **PASS**.
  - Python Check (Python 3.12; dependency/install, compile, import smoke, full suite): **PASS**.
  - Python tests (Python 3.11.15): **PASS**.
  - `pip check`: **PASS — no broken requirements**.
  - `python -m compileall -q sniperplug tests *.py`: **PASS**.
  - Full Python 3.11 suite: **1,161 passed, 1 upstream `audioop` deprecation warning**.
- The final task-record-only commit must also remain green before PR #232 is merged.

## Cleanup and conflict inspection
- Branch comparison after implementation validation: **0 commits behind `main`**.
- Changed-file scope is limited to `ACTIVE_TASK.md`, personal-DM command/menu/model/matcher/delivery/telemetry code, the three retained DM fanout paths, and directly related tests; no provider scanner, public threshold, catalog discovery, or unrelated retailer logic is changed.
- The former direct per-fanout total-cap checks and direct receipt writers were integrated into the shared policy rather than left as conflicting duplicate implementations.
- The global autoscan architecture regression now requires the cap implementation to live in the shared delivery policy instead of preserving an obsolete direct-cap string in legacy fanout.
- Durable family receipt conflicts use `DO NOTHING`; a duplicate cannot move an old delivery into a newer day's family count.
- PR #232 has no inline review threads and no submitted review findings at the implementation-validation checkpoint.
- No second matcher, in-memory-only diversity counter, fake oversized Unlimited cap, or retailer-specific diversity service remains.

## Current branch / PR
- Branch: `fix/dm-alert-diversity-unlimited`
- PR: #232 — `Balance personal DM categories and add Unlimited alert caps`

## Deployment boundary
- This task changes runtime DM behavior and adds an idempotent durable table. Code-side completion does not claim production observation before merged `main` is deployed.
- Existing same-day canonical receipts created before this feature have no family-history companion row, so family balancing becomes fully representative as new sends are recorded; the finite total daily cap remains authoritative during that transition.

## Blockers
- None in implementation. Merge is gated only by final-head CI/current-branch checks.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` only after SP-DM-DIVERSITY is merged and the task lock is released.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings during Walmart drain-mode exact verification.
- Investigate the high volume of Walmart `seller` identity blocks and transient exact-fetch failures.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into unrelated work.
