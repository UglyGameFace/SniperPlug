# Active Task

## Task / outcome
**SP-MOVIE-TICKETS-RECOVERY — restore SniperPlug movie tickets end-to-end and keep the existing July 31 permanent panel truthful and usable.**

Target outcome: current public movie offers/screening leads are discovered, freshness-qualified, truthfully labeled, optionally filtered by a user's ZIP/radius, and rendered through the existing permanent panel without Discord rejecting the response.

## Scope
`public source discovery → fail-closed parse → source health/backoff/fallback → durable current cache → poster enrichment → actionable panel/latest/status → personal ZIP/local view → configured alert delivery → feedback controls`

Do not switch to unrelated Walmart, watcher, gateway, setup, DM, site, Minecraft, or other work without an explicit FORCE SWITCH.

## Status
**PR #272 is the single active draft.**

Already completed under this task:
- #268 fixed Atom indexed recovery and passed exact-head Termux/Ubuntu Python 3.12 validation. Production now truthfully reports Atom `Indexed fallback current` when direct Atom HTTP access is blocked, without inventing public codes.
- #271 fixed current Gofobo image-card extraction. Exact head `8d5153d8730c96302f0d91aef6da07654fc0e3c6` passed targeted tests, live Gofobo acceptance, native libsql checks, full suite **1,377 passed**, diff/scope/cleanliness checks, then merged as `60cc3f8b55d2543cfa396105f189ec2427462ca7` and was deployed.
- Production DB after #271 contained **4 Gofobo + 3 SeeItEarly = 7 active screening/discovery rows**, matching the live source evidence.

Production acceptance then exposed the next same-task failure: permanent-panel Check Now is acknowledged safely but Discord rejects the follow-up with HTTP 400 / error 50035: `In embeds: Embed size exceeds maximum size of 6000`.

## Root cause / execution path
The authoritative production manual-result paths are:
- registered `/movies latest` in `registered_multi_source_movies.py`;
- permanent-panel Check Now in `movie_command_guide.py`;
- personal Local / Show All rendering in `movie_result_actions.py`.

All three build up to eight detailed result embeds, call `annotate_movie_result_embeds`, then send those embeds in one Discord message. Individual cards are legal, but the aggregate character count of all embeds in one message can exceed Discord's 6000-character message-level embed limit. Restoring four Gofobo cards raised the current seven-result payload above that limit.

Automatic issuer alerts send individual result cards and are not affected by this aggregate limit.

## #272 implementation
Branch: `fix/movie-embed-budget-272`

Current head before validation: `fa836ac40f9f396c8cc41ed6471ee7afe7f8dccb` plus this task-record commit.

Implementation is intentionally centralized in the authoritative shared manual-result helper instead of adding per-button retries or duplicate send paths:
- detailed result cards remain unchanged when the combined payload is legal;
- if combined embed characters exceed Discord's 6000 hard limit, all result cards are compacted under a reserved **5800-character** aggregate budget;
- no result is dropped;
- compact cards preserve movie identity, numbered result/button mapping, direct URL, poster/thumbnail, actual promo code when present, classification, a verification warning, and source identity;
- list identity remains stable for existing callers;
- a development-time invariant fails closed if future compact-card changes could still exceed the reserved aggregate budget.

## Regression coverage
`tests/test_movie_result_actions_264.py` now creates eight oversized Gofobo-style cards that start above Discord's 6000-character limit and verifies:
- all eight results remain present;
- final aggregate size is <= 5800 and < 6000;
- numbered card/action mapping remains intact;
- movie titles remain visible;
- poster imagery survives compaction.

Existing result-action, permanent-panel, slash-latest, source URL, and feedback-control regressions remain in place.

## Validation / results
### #271
Exact head `8d5153d8730c96302f0d91aef6da07654fc0e3c6`:
- targeted movie/Gofobo regressions passed;
- live Gofobo parser acceptance passed;
- `pip check`, `compileall`, import smoke, native libsql process tests passed;
- full pytest **1,377 passed, 1 existing audioop deprecation warning**;
- diff check, exact changed-file scope, conflict scan, clean worktree, exact-head verification passed.

### #272
**Not yet exact-head validated.** GitHub-hosted Actions quota remains unavailable, so validation must run in the known-good Termux/Ubuntu Python 3.12 environment with native libsql.

Required validation:
- targeted movie result-action / permanent-panel / location / Gofobo regressions;
- production-shape seven/eight-card embed-budget probe;
- `pip check`;
- `compileall`;
- import smoke;
- native libsql process tests;
- full pytest;
- `git diff --check`;
- exact changed-file scope;
- conflict-marker scan;
- clean worktree;
- exact-head lock.

## Cleanup / conflicts
- No retry loop, Discord special-case fallback send, monkey patch, duplicate renderer, or second movie implementation was added.
- The existing registered multi-source cog remains authoritative.
- #272 changes only the shared manual result renderer, its regression tests, and this active-task record.
- Unrelated Walmart API failures visible in production logs remain backlogged and are not being investigated while this task is active.

## Risks / boundaries
- Discord's aggregate embed character limit applies across all embeds in one message; individual per-embed field limits still apply independently.
- Compaction intentionally reduces repeated explanatory boilerplate only when necessary to make the payload legal. It must never manufacture availability, location, or promo-code claims.
- Production acceptance still requires the same July 31 panel to complete Check Now successfully with the current seven-result dataset.

## Backlog — not active
- SP-WM-DEAL-DELIVERY-STALL / current Walmart API exact-price failures
- SP-GATEWAY-EVENT-LOOP-LAG
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Freeze the exact #272 head after this task-record update, run the full Termux/Ubuntu Python 3.12 validation set, then only if green mark #272 ready, merge that exact head, deploy the merge to the existing SniperPlug Discloud app, and re-run Check Now / Show All / Status on the original July 31 panel. Do not call the movie-ticket task restored until that production interaction succeeds and the displayed counts still match the production database.