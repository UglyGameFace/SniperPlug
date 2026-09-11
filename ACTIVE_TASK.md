# Active Task

## Status
**SP-WM-EXACT-RECOVERY-DEBT — IN PROGRESS.**

The user authorized moving here once `SP-GATEWAY-EVENT-LOOP-LAG` was solved. PR #284 is now production-accepted and the active lock moves to the remaining Walmart exact-recovery debt.

Base main at task start: `adc83678b4d1f8c6d7c0fdb615a82bf128889327` (PR #284 merge).

## Completed prerequisite — SP-GATEWAY-EVENT-LOOP-LAG
PR #284 is production-accepted.

A real recurrence supplied the decisive evidence:

- discord.py emitted `Can't keep up, shard ID None websocket is 41.5s behind`;
- the v284 correlation monitor simultaneously reported `classification=gateway_ack_latency_loop_healthy`, `gateway_latency_s=41.55`, and only `recent_loop_lag_max_s=0.29` while Walmart catalog/exact workers were active;
- the monitor later reported heartbeat latency recovered to `0.01s` with local loop lag still `0.29s`;
- the watchdog remained alive and subsequent health samples stayed around `0.28-0.32s` local lag;
- the Discord warning was no longer duplicated by two log handlers.

That proves the residual 41-second event is Discord heartbeat ACK/network-path latency, not SniperPlug event-loop starvation. The app-side synchronous logging defect was removed and the remaining event is now truthfully classified. Do not throttle Walmart or reopen the gateway task based solely on another `Can't keep up` ACK-latency warning when the local loop remains healthy.

## Current production symptom
Walmart delivery/fanout is healthy, but the exact queue still spends substantial work on low-value recovery debt:

- repeated `api_price_missing->api_price_missing` batches;
- bounded pressure repeatedly shows `confirmed no-price retries >=600`;
- occasional `api_price_missing->api_http_400` Product Lookup failures;
- rare product-page `page_timeout` failures;
- fresh catalog proof can simultaneously verify 8/8 or 24/24 rows and fan out valid deals, so this is not another delivery-pipeline failure.

## Root-cause finding for first fix
A confirmed dual no-price row means the primary official exact-detail path and the exact-ID Affiliate Product Lookup path both returned the requested item without a canonical current purchase price. PR #277 reduced those rows to a six-hour retry, but after six hours the queue deliberately makes the same two official requests again forever. Production has now accumulated at least 600 such due rows.

The queue already has a safe wake mechanism: ordinary weak/cosmetic rediscovery preserves a confirmed-no-price schedule, while a complete fresh selected-offer snapshot or a material apparent current/reference-price change shortens `next_attempt_at` to now. Therefore unchanged dual-no-price rows do not need another exact network request every six hours merely to prove the same absence again.

The first remediation will park only rows whose persisted `next_attempt_at` still exactly represents the untouched six-hour #277 schedule. A row whose schedule was shortened by fresh/material discovery evidence must remain immediately actionable, even when that wake occurs more than six hours after the prior exact attempt.

## Implementation scope — PR #285
1. Add a bounded pre-claim dormancy pass for mature confirmed dual-no-price retries.
2. Match only the exact untouched six-hour #277 schedule, not every timestamp later than the six-hour floor.
3. Move matched due rows to a 30-day safety reprobe instead of performing the same exact requests again every six hours.
4. Keep status/error history intact so existing catalog rediscovery logic can still wake the row immediately on complete fresh proof or a material current/reference change.
5. Exclude active leases and cap each pass at 200 rows so the existing >=600 population drains from actionable pressure in bounded chunks.
6. Emit `marker=confirmed_no_price_dormancy_v285` with parked-row count for production acceptance.
7. Do not change current-price trust, reference trust, seller/offer/variant/marketplace/shipping proof, thresholds, fanout, page-circuit behavior, or ordinary transient retry cadence.

## Follow-up inside this active task
After the no-price population is no longer wasting exact capacity, use production evidence to resolve the smaller failure classes rather than mixing guesses into this patch:

- make Product Lookup HTTP 400 diagnostics readable enough to identify the actual Walmart validation/error response before changing request semantics;
- inspect the remaining `page_timeout` / page-recovery population separately. A rare timeout alone is not evidence for weakening the existing one-probe pacing or page challenge circuit.

## Validation required
- targeted PR #285 no-price dormancy regressions;
- prior #277-#282 Walmart pressure/claim/wake regressions;
- bulk persistence/runtime regressions;
- native libsql process tests;
- compile/import/pip checks;
- full pytest suite;
- diff/scope/conflict-marker/clean-worktree checks on one exact head;
- production acceptance showing the v285 marker, confirmed-no-price actionable pressure falling sharply from the current >=600 wave, ordinary/fresh work remaining claimable, and a material/fresh discovery wake still bypassing dormancy.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's earlier FORCE SWITCH. Preserve PR #273 and all merged movie-ticket work unchanged.

## Backlog — not active
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Implement PR #285 on `fix/walmart-confirmed-no-price-dormancy-285`, validate one exact head, then production-check the >=600 no-price lane before changing the smaller HTTP-400/page-timeout paths.
