# Active Task

## Status
In progress on branch `fix/delivery-audit-and-multiretailer-fanout`.

## Task
**SP-DELIVERY-223 — Repair sparse DM/public deal delivery observability and multi-retailer fanout enrollment**

## Scope
1. Keep exact item/seller/offer/variant/shipping/reference proof fail-closed and unchanged.
2. Keep Walmart's 10% discovery floor separate from each destination's public threshold; do not broaden public posting policy accidentally.
3. Make DM fanout explain why exact deal events did not become DMs: filter rejection, duplicate receipt, daily cap, closed/unavailable DMs, or delivery error.
4. Surface DM preferences checked and matches in normal fanout summaries instead of printing only `DM sent`.
5. Repair the live public-destination loader so HP/Target verified-event fanout is not incorrectly gated on Walmart being enabled.
6. Add focused regressions for retailer enrollment and DM delivery accounting.
7. Run compile/static validation and available automated regression checks before completion.

## Findings
- Production logs supplied 2026-08-08 show global Walmart discovery progressing continuously and the exact queue repeatedly draining to zero actionable rows.
- Both current Walmart public destinations are enrolled and fanout executes without Discord send errors.
- The visible fresh fanout examples were only 18.35% and 11.58% off, correctly below the two public thresholds (50% and 40%).
- `GlobalDealFanoutResult` already stores `dm_preferences_checked` and `dm_matches`, but its summary omits both.
- DM non-matches, duplicate receipts, and daily-cap skips currently use bare `continue` paths with no counters explaining the loss.
- `list_live_public_alert_guilds()` hard-requires `walmart` in configured retailers. `verified_retailer_event_fanout.py` reuses that loader for HP and Target, so an otherwise valid HP/Target destination can be skipped solely because Walmart is disabled.
- Exact verification logs contain frequent missing-seller identity blocks and transient failures. Those are upstream proof failures, not Discord delivery failures, and remain backlog for a separate exact-verification task unless this task exposes a direct regression.

## Changes
- Pending.

## Validation
- Pending.

## Cleanup and conflict inspection
- Pending.

## Current branch
`fix/delivery-audit-and-multiretailer-fanout`

## Deployment boundary
- Canonical Discloud app: `1779293887444`.
- Duplicate app `1785806676351` must never be targeted.
- Do not claim production completion until merged `main` is deployed to the canonical app and live logs confirm the new delivery accounting.

## Backlog
- Investigate the high volume of Walmart `seller` identity blocks and transient exact-fetch failures after delivery-path work is complete.
- Verify the already-merged catalog queue write-amplification fix remains healthy in production; current supplied logs show the expected mostly-unchanged rows and one write statement per batch.
- PR #200 remains isolated and must not be mixed into this task.
