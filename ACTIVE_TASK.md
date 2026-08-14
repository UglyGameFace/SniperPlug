# Active Task

## Status
**Active — SP-DEALS-DISCOVER-HARDENING-244 live follow-up. PR #245 merged, main CI passed, and the merged build was deployed. The second live `/deals` test improved initial response time but still failed the button gate: some mode controls returned Discord `SniperPlug didn't respond in time`, and the result presentation remained too dense. Focused follow-up implementation is in progress on `fix/deals-live-timeout-245`.**

## Scope
Finish the existing `/deals` + `/discover` hardening task. Do not start the suspended Walmart missed-deals backlog until this live gate passes.

## Current live findings
- Merged commit `84db8b9` deployed successfully and startup was healthy.
- `/deals` now renders without the previous 10+ minute wait, so the bounded interactive-search work helped.
- Mode controls are still not reliable: Hidden Gems and additional mode buttons can produce Discord `SniperPlug didn't respond in time`.
- The current View creates another control message after every mode switch while reusing the same static custom IDs across all menus; this creates unnecessary competing View instances and a stale-dispatch surface.
- Button acknowledgement is split across individual callbacks instead of one earliest View-level path; failed acknowledgement currently returns silently with no task-specific diagnostic.
- The user-facing summary/menu and Walmart proof cards expose too much internal API/audit detail for the normal `/deals` shopping flow even though the price data itself is useful.

## Focused follow-up implementation
- Give each `/deals` result menu a unique token in every component custom ID.
- Acknowledge every component in `DealSearchModeView.interaction_check` using the component-native deferred message update before any button-specific work.
- Add task-specific acknowledgement and callback-error logs instead of swallowing component failures.
- Keep one control-panel message: mode changes edit the existing menu instead of spawning duplicate menus.
- Make the selected mode visually obvious; only the selected mode is primary/disabled.
- Keep Fresh Scan bounded and reuse the same menu token/owner across refreshes.
- Add `/deals`-only compact result embeds that preserve price, seller, delivery, links, verified/private status and thumbnails while omitting internal identity/evidence noise. Autoscan/public proof rendering is unchanged.
- Compress the normal finder summary and controls menu; deep diagnostics remain available in diagnostic/health surfaces rather than the beginner command.

## Validation required
1. Focused control/UX regressions pass.
2. Existing PR #244/#245 deal/discover regressions pass.
3. `pip check`, compileall, and diff/static validation pass.
4. Full Python 3.11 test suite passes.
5. Final branch conflict/cleanup inspection shows no temporary workflow or duplicate implementation.
6. PR checks pass, review/comments/threads are clean, squash merge, and post-merge `main` checks pass.
7. Deploy merged main to Discloud app `1779293887444`.
8. Live `/deals search:phone mount`: initial result is responsive; every enabled mode button responds; Fresh Scan responds; one control panel remains current; compact embeds are readable.
9. Live `/discover` still returns only current-job item IDs as established by PR #244.
10. Only then close SP-DEALS-DISCOVER-HARDENING-244.

## Suspended task
**SP-WM-MISSED-DEALS-236 remains suspended, not closed.** Resume only after this active task closes or an explicit FORCE SWITCH.

## Backlog
- Resume Walmart exact-verification/page-recovery throughput work after #244 closes.
- Investigate remaining Discord gateway lag if it persists after this command path is stable.
- Resume SniperPlug-Site Whop content-reader work later.
