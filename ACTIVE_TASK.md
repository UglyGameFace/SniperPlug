# Active Task

## Status
**Active — SP-DM-DIVERSITY.** PR #231 merged successfully and closed SP-TURSO-STARTUP after production validation. Work is isolated on branch `fix/dm-alert-diversity-unlimited`.

## Task
**SP-DM-DIVERSITY — Prevent broad Smart DM alerts from being monopolized by apparel/fashion deals and add a true Unlimited daily-limit option**

## Scope
1. Preserve every existing hard personal filter, exact-price proof requirement, dedupe rule, retailer/location guard, favorite, mute, hard allowlist, and strict Flip Override behavior.
2. Stop broad Smart alerts with a finite daily cap from letting one category family consume the user's available DM slots merely because those events arrived first.
3. Treat `premium_apparel`, `shoes_apparel`, and `sneakers` as one fashion family for delivery balancing so aliases cannot bypass the guard.
4. Keep intentional user choices authoritative: hard allowlists, favorite categories, and strict Flip Override opportunities are not suppressed by automatic diversity balancing.
5. Add one shared durable delivery policy used by live Walmart bulk fanout and verified HP/Target event fanout; retain compatible semantics in the legacy Walmart fanout implementation.
6. Add a real Unlimited personal-DM option. Unlimited removes the total daily alert cap rather than encoding a fake large number; it must survive normalization/storage and display clearly in the UI.
7. Make diversity state durable across process restarts rather than relying on in-memory counters.
8. Add focused regression coverage, compile/static validation, full-suite validation, cleanup, and conflict inspection before claiming code-side completion.

## Findings
- `sniperplug/services/walmart_global_deal_fanout_bulk.py` is the live Walmart scheduled fanout imported by `global_auto_scan_runner.py`.
- Walmart bulk fanout evaluates verified events in queue/FIFO order and immediately sends each matched DM until the user's total daily receipt count reaches `max_alerts_per_day`.
- `sniperplug/services/verified_retailer_event_fanout.py` uses the same total-count-only behavior for HP/Target verified events.
- No shared delivery-diversity guard currently exists, so an apparel-heavy verified wave can consume a finite daily cap before later tech/home/gaming events arrive.
- The current preference model clamps `max_alerts_per_day` to 1–100, the UI rejects values outside 1–100, and `_preference_from_row` converts a stored zero back to the default. Therefore Unlimited does not exist today.
- Existing DM receipts persist only `user_id`, `deal_key`, and `delivered_at`; category-family counts cannot currently survive a restart.
- The live matcher already knows category keys, favorites, hard allowlists, mutes, and strict flip qualification, so the delivery policy can preserve those decisions instead of guessing from titles a second time.

## Planned implementation
- Use `0` as the canonical durable Unlimited sentinel while retaining 25/day as the default for existing users.
- Add explicit UI parsing/display for `unlimited`/equivalent no-cap terms rather than exposing the sentinel as a magic number.
- Add durable per-delivery category-family receipt metadata without destructively altering the existing receipt table.
- Add one shared DM delivery gate that enforces the finite total cap first, then applies family balancing only to broad Smart alerts where slot scarcity exists.
- Unlimited subscribers receive every otherwise-qualifying deal; diversity balancing does not silently turn Unlimited into another hidden cap.
- Integrate the shared gate into Walmart bulk, verified-retailer, and retained legacy Walmart DM delivery paths.

## Validation
- Pending implementation.

## Cleanup status
- No implementation changes have been made yet beyond this task record.
- No second matcher or retailer-specific diversity policy will be introduced.

## Blockers
- None currently.

## Backlog
- Resume the SniperPlug-Site Whop app-specific content-reader task for Hidden Files experiences such as `Make Money Here`, `Content`, and `Better Content` only after SP-DM-DIVERSITY satisfies Definition of Done.
- Investigate repeated Discord gateway `Can't keep up ... websocket is 41.5s behind` warnings during Walmart drain-mode exact verification.
- Investigate the high volume of Walmart `seller` identity blocks and transient exact-fetch failures.
- Continue verifying PR #223 delivery-health accounting from live post-deploy logs.
- PR #200 remains isolated and must not be mixed into unrelated work.
