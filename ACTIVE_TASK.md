# Active Task

## Status
**Active — SP-DEALS-DISCOVER-HARDENING-244 live follow-up. PR #244 merged and its merge checks passed, but the production `/deals search:phone mount` gate failed. Branch `fix/deals-live-timeout-245` addresses the live latency and dead-button failure before #244 can close.**

## Scope
Finish the existing `/deals` + `/discover` hardening task. Do not start the suspended Walmart missed-deals backlog until this live gate passes.

## Live findings
- The `/deals search:phone mount` result menu took more than ten minutes to arrive.
- The mode menu then showed Discord `SniperPlug didn't respond in time`; by the screenshot the menu was 13 minutes old.
- `DealSearchModeView` used a hard-coded 300-second timeout, so the visible buttons became dead after five minutes.
- `find_walmart_deals_for_query` built up to 200 provider routes: 10 expanded queries × 4 sort passes × 5 pages, then awaited the entire gather before rendering the menu.
- Manual `/deals` searches and background Walmart catalog searches shared the same search-priority lane. Live logs showed Walmart capacity 3 saturated with 5-6 waiting searches plus Discord gateway/event-loop lag.

## Current implementation
- Bound interactive `/deals` to at most eight provider routes while keeping the exact query first, two exact-query relevance pages, price/bestseller coverage, and top expanded routes. `/discover` remains the deep crawler.
- Reduce `/deals` local fanout concurrency from 8 to 4 so one command cannot flood the shared Walmart request gate.
- Add an interactive Walmart search priority between exact verification and background catalog/search work. Real Discord user IDs use it; exact verification still has highest priority.
- Extend the mode menu lifetime from five minutes to fifteen minutes.
- Use safe interaction defer handling for mode and New/Drops callbacks.
- Add regressions for the eight-route budget, user-vs-background request priority, interactive waiter ordering, and 15-minute view lifetime.

## Definition of Done
1. Targeted regressions pass.
2. `pip check`, compileall, and the full pytest suite pass.
3. Temporary applicator workflow is absent from the final branch diff.
4. Branch is clean against current `main`; inspect conflicts and duplicate/obsolete implementations.
5. Create PR, require canonical PR checks green, inspect comments/reviews/inline threads, then merge.
6. Verify the merge commit checks on `main`.
7. Deploy merged `main` to Discloud app `1779293887444`.
8. Live `/deals search:phone mount` must render in a reasonable interactive window, not 10+ minutes; mode buttons must work after the menu appears; Fresh Scan remains bounded.
9. Live `/discover` must still return only current-job item IDs as established by PR #244.
10. Only then close SP-DEALS-DISCOVER-HARDENING-244.

## Cleanup status
Pending validation. No duplicate verifier or alternate `/deals` command is being added. The deep `/discover` path is intentionally left separate.

## Suspended task
**SP-WM-MISSED-DEALS-236 remains suspended, not closed.** Resume only after this active task closes or an explicit FORCE SWITCH.

## Backlog
- Resume Walmart exact-verification/page-recovery throughput work after #244 closes.
- Investigate remaining Discord gateway lag only if it persists after interactive `/deals` request volume is bounded.
- Resume SniperPlug-Site Whop content-reader work later.
