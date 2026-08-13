# Active Task

## Status
**Active — SP-DEALS-DISCOVER-HARDENING-244 live follow-up. PR #244 merged and its merge checks passed, but the production `/deals search:phone mount` gate failed. The focused follow-up on `fix/deals-live-timeout-245` is implemented and fully validated; PR/merge/deploy/live proof remain before #244 can close.**

## Scope
Finish the existing `/deals` + `/discover` hardening task. Do not start the suspended Walmart missed-deals backlog until this live gate passes.

## Live findings
- The `/deals search:phone mount` result menu took more than ten minutes to arrive.
- The mode menu then showed Discord `SniperPlug didn't respond in time`; by the supplied screenshot the menu was 13 minutes old.
- `DealSearchModeView` used a hard-coded 300-second timeout, so the visible buttons became dead after five minutes.
- `find_walmart_deals_for_query` built up to 200 provider routes: 10 expanded queries × 4 sort passes × 5 pages, then awaited the entire gather before rendering the menu.
- Manual `/deals` searches and background Walmart catalog searches shared the same search-priority lane. Live logs showed Walmart capacity 3 saturated with 5-6 waiting searches plus Discord gateway/event-loop lag.

## Implementation
- Bound interactive `/deals` to at most eight provider routes while keeping the exact query first, two exact-query relevance pages, price/bestseller coverage, and top expanded routes. `/discover` remains the deep crawler.
- Reduced `/deals` local fanout concurrency from 8 to 4 so one command cannot flood the shared Walmart request gate.
- Added an interactive Walmart search priority between exact verification and background catalog/search work. Real Discord user IDs use it; exact verification still has highest priority.
- Extended the mode menu lifetime from five minutes to fifteen minutes.
- Routed mode and New/Drops interaction acknowledgement through the existing safe defer helper.
- Added regressions for the eight-route budget, user-vs-background request priority, interactive waiter ordering, and 15-minute view lifetime.
- Preserved the established `exact_has_priority` architecture marker after the first full-suite run exposed a static compatibility assertion; the new exact > interactive > background behavior remains intact.

## Validation
- First focused run: 13 targeted tests passed; `pip check`, compileall, and `git diff --check` passed. Full suite reached **1,227 passed / 1 failed**; the only failure was the existing static assertion requiring the `exact_has_priority` source marker.
- Compatibility-preserving rerun: **15 targeted tests passed**.
- `pip check`: PASS — no broken requirements.
- `python -m compileall -q sniperplug tests *.py`: PASS.
- `git diff --check`: PASS.
- Full Python 3.11 suite: **1,228 passed**, one upstream `discord.py` `audioop` deprecation warning.
- Final branch compare against `main`: behind **0**, ahead only by the focused task commits.

## Cleanup / conflict inspection
- Temporary applicator workflows removed from the final branch diff.
- Final changed-file set is limited to `ACTIVE_TASK.md`, `sniperplug/cogs/unified_deal_scanner.py`, `sniperplug/providers/coordinated_walmart.py`, `sniperplug/services/deal_finder_engine.py`, `sniperplug/services/walmart_request_coordinator.py`, and `tests/test_deals_live_timeout_245.py`.
- No duplicate verifier, alternate `/deals` command, parallel deep crawler, or proof-gate implementation was added.
- `find_walmart_deals_for_query` has one runtime caller: the unified `/deals` path. `/discover` continues through its separate deep verified-discovery path.
- No Walmart discount/reference/seller/offer/variant/shipping/public/DM eligibility gate was weakened.

## Remaining Definition of Done
1. Create PR from `fix/deals-live-timeout-245` into `main`.
2. Require canonical PR checks green and inspect comments/reviews/inline threads; resolve any real finding.
3. Merge only after PR validation is green, then verify the real `main` merge-commit checks.
4. Deploy merged `main` to Discloud app `1779293887444`.
5. Live `/deals search:phone mount` must render in a reasonable interactive window, not 10+ minutes; mode buttons must work after the menu appears; Fresh Scan remains bounded.
6. Live `/discover` must still return only current-job item IDs as established by PR #244.
7. Only then close SP-DEALS-DISCOVER-HARDENING-244.

## Suspended task
**SP-WM-MISSED-DEALS-236 remains suspended, not closed.** Resume only after this active task closes or an explicit FORCE SWITCH.

## Backlog
- Resume Walmart exact-verification/page-recovery throughput work after #244 closes.
- Investigate remaining Discord gateway lag only if it persists after interactive `/deals` request volume is bounded.
- Resume SniperPlug-Site Whop content-reader work later.
