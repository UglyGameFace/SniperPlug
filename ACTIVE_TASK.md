# Active Task

## Status
Implementation and validation complete on branch `fix/delivery-audit-and-multiretailer-fanout`. PR #223 is merge-ready; production remains unclaimed until merged `main` is deployed to the canonical Discloud app and verified from live logs.

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
- `GlobalDealFanoutResult` already stored `dm_preferences_checked` and `dm_matches`, but its summary omitted both.
- DM non-matches, duplicate receipts, and daily-cap skips used bare `continue` paths with no counters explaining the loss.
- `list_live_public_alert_guilds()` hard-required `walmart` in configured retailers. `verified_retailer_event_fanout.py` reused that loader for HP and Target, so an otherwise valid HP/Target destination could be skipped solely because Walmart was disabled.
- Exact verification logs contain frequent missing-seller identity blocks and transient failures. Those are upstream proof failures, not Discord delivery failures, and remain backlog for a separate exact-verification task.

## Changes
- Made live public-destination loading retailer-aware while preserving Walmart as the default scheduler contract.
- HP/Target verified-event fanout now loads all live enabled public destinations; the canonical HP/Target posting functions remain responsible for retailer-specific enablement.
- Walmart fanout summaries now expose DM checked/matched/sent plus filter/duplicate/daily-cap skips and DM-disabled counts.
- Verified HP/Target fanout summaries now expose DM checked/matched/sent and guild checks/posts.
- Added process-local per-user DM delivery telemetry for proof, price, Walmart Cash, keyword, category, discount, score, savings, location, duplicate, cap, disabled-DM, send-error, and sent outcomes.
- Added `📬 Delivery health` to `/dm_deals action:Show my settings`, including today's durable receipt count/cap plus the since-restart reason audit.
- Telemetry intentionally adds no database write for rejected candidates, preventing delivery diagnostics from recreating remote DB write pressure.
- Kept `match_dm_deal()` pure; delivery telemetry is recorded only at the real Walmart/HP/Target fanout boundaries.
- Deleting personal DM alert data also clears the process-local delivery audit.

## Validation
- GitHub Import smoke check: **PASS** on head `7ddd53c3bfdcede5bbb43a9ac227e76acf9ad457`.
- GitHub Python Check: **PASS** on the same head, including compile, import smoke, and full tests.
- GitHub Python tests: **PASS** on the same head, including `pip check`, `python -m compileall -q sniperplug tests *.py`, and `pytest -q`.
- The first full run exposed three compatibility/test failures; all three were corrected: preserved the existing live-loader contract phrase, preserved the scheduler membership call signature by relying on the Walmart default, and classified Walmart Cash before generic `proof`.
- Full suite contains 1,119 tests; the pre-fix run reported 1,116 passed/3 failed, and the corrected head reran the same suite successfully.

## Cleanup and conflict inspection
- Removed an unused generic `DmDeliveryCounters` abstraction before completion.
- Restored the generic DM matcher to its pre-task pure implementation so tests/legacy callers cannot inflate real delivery telemetry.
- Current diff is limited to 9 task-relevant files.
- Branch comparison: ahead of `main` by 17 commits, behind by 0; no unrelated base changes were pulled into the task.
- Exact seller/offer/reference proof, Walmart 10% discovery floor, per-server public thresholds, Smart/All/Custom filter semantics, and public duplicate protection were not loosened.

## Current branch / PR
- Branch: `fix/delivery-audit-and-multiretailer-fanout`
- PR: #223 — `Repair sparse deal delivery observability and multi-retailer fanout`

## Deployment boundary
- Canonical Discloud app: `1779293887444`.
- Duplicate app `1785806676351` must never be targeted.
- Do not claim production completion until merged `main` is deployed to the canonical app and live logs confirm the new delivery accounting.

## Backlog
- Investigate the high volume of Walmart `seller` identity blocks and transient exact-fetch failures as the next separate task.
- Verify the already-merged catalog queue write-amplification fix remains healthy in production; current supplied logs show the expected mostly-unchanged rows and one write statement per batch.
- PR #200 remains isolated and must not be mixed into this task.
