# Active Task

## Status
**Complete — SP-DM-DIVERSITY satisfied its code-side Definition of Done and PR #232 was merged into `main` at `c9392927bb7510720e9dc1ac203c484c0e8bce5c`.** Production observation is intentionally unclaimed until merged `main` is deployed and live DM traffic exercises the new policy.

## Task
**SP-DM-DIVERSITY — Prevent broad Smart DM alerts from being monopolized by apparel/fashion deals and add a true Unlimited daily-limit option**

## Root cause / findings
- Live Walmart bulk fanout and verified HP/Target fanout consumed each subscriber's finite daily cap in event/FIFO order with only a total sent-today check.
- No shared delivery-diversity guard existed, so an apparel-heavy verified wave could consume finite alert slots before later tech/home/gaming/tools events arrived.
- The prior preference model clamped `max_alerts_per_day` to 1–100 and converted a stored zero back to the default, so a real Unlimited state did not exist.
- Existing canonical DM receipts stored only `user_id`, `deal_key`, and `delivered_at`, so category-family delivery history could not survive a restart.
- The live matcher already exposed the information needed to preserve favorites, hard allowlists, mutes, strict hard filters, and Flip Override qualification without guessing user intent again in fanout.

## Changes delivered
- `0` is the canonical durable Unlimited sentinel while 25/day remains the default for existing users.
- The personal DM menu and typed `/dm_deals` setup accept Unlimited input and display `unlimited`; Unlimited bypasses both the total daily cap and automatic family quota.
- Added one shared `sniperplug/services/dm_delivery_policy.py` used by live Walmart bulk fanout, verified HP/Target fanout, and retained legacy Walmart fanout.
- `premium_apparel`, `shoes_apparel`, and `sneakers` collapse into one `fashion` family for automatic balancing.
- Broad Smart alerts with a finite cap reserve room for category variety. At the default 25/day, fashion receives at most 7 automatic slots.
- Hard category allowlists, favorite categories, non-Smart modes, and strict Flip Override opportunities bypass automatic diversity balancing while all pre-existing hard personal filters remain enforced.
- Added a per-user async delivery lock so retailer fanouts cannot race each other past the same finite cap inside one process.
- Added durable `user_dm_deal_alert_family_receipts` history with the same 90-day retention window as canonical receipts.
- Successful sends persist canonical dedupe and family history in the same transaction; duplicate receipt timestamps remain immutable.
- Added `priority_override` to the match decision so strict flip qualification is explicit rather than inferred from display text.
- DM delivery telemetry and fanout summaries now report diversity-reserve skips separately from filter rejects, duplicates, and total-cap skips.
- Explicit DM-data deletion removes the durable family history too.

## Validation
- Focused regressions cover Unlimited parsing/normalization/display, fashion-family aliases, finite family quotas, Unlimited bypass semantics, total-cap precedence, favorite/allowlist/non-Smart/strict-flip exceptions, all retained fanout integrations, telemetry, and an in-memory SQLite schema/persistence/reinitialization round trip.
- An initial full-suite run exposed one stale observability assertion that expected the old three-part skip summary; the test was corrected to require the new diversity bucket. No runtime weakening was used to make CI pass.
- Implementation head `0455945b24c6f71c3454617b9a4a1668f9eec1ad`: Import smoke **PASS**, Python Check **PASS**, Python 3.11 tests **PASS**, `pip check` **PASS**, compileall **PASS**, full suite **1,161 passed** with one upstream `audioop` deprecation warning.
- Final documentation head `b4c9e546be8d0a1a8c51ffe0ab731b099514c280`: all three required GitHub workflows **PASS** again before merge.

## Cleanup and conflict inspection
- Final branch comparison before merge: **0 commits behind `main`**.
- Changed scope was limited to this task record, personal-DM command/menu/model/matcher/delivery/telemetry code, the three retained DM fanout paths, and directly related tests.
- Former direct per-fanout cap checks/receipt writers were integrated into the shared policy rather than left as conflicting duplicate implementations.
- No second matcher, in-memory-only diversity counter, fake oversized Unlimited cap, or retailer-specific diversity service remains.
- PR #232 had no inline review threads and no submitted review findings before merge.

## Merge / deployment boundary
- PR #232: `Balance personal DM categories and add Unlimited alert caps` — **merged**.
- Merge commit: `c9392927bb7510720e9dc1ac203c484c0e8bce5c`.
- Code-side task lock is released.
- Production observation remains separate: do not claim live behavior until merged `main` is deployed and real DM traffic is observed.
- Existing same-day canonical receipts created before deployment have no family-history companion row, so family balancing becomes fully representative as new sends are recorded; the finite total cap remains authoritative during that transition.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content`.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings during Walmart drain-mode exact verification.
- Investigate the high volume of Walmart `seller` identity blocks and transient exact-fetch failures.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into unrelated work.
