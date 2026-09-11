# Active Task

## Status
**SP-WM-EXACT-RECOVERY-DEBT — IN PROGRESS.**

The active lock remains Walmart exact-recovery debt until the remaining queue/recovery failure classes are resolved from production evidence. Do not switch to unrelated work without the user's explicit FORCE SWITCH instruction.

Base main for the current follow-up: `4da84f6baffe66154b2bcd6771fa0c92726fa3ae` (PR #287 merge).

## Completed prerequisite — SP-GATEWAY-EVENT-LOOP-LAG
PR #284 remains production-accepted.

The latest supplied window again contained ~41-second Discord heartbeat warnings, but local loop lag remained below one second (`recent_loop_lag_max_s` up to 0.59s), the watchdog classified them `gateway_ack_latency_loop_healthy`, and latency recovered normally. Do not reopen or throttle Walmart based solely on those ACK/network-path events while local event-loop telemetry remains healthy.

## PR #285 production acceptance — confirmed no-price debt
PR #285 remains live and working as intended.

Production is parking mature dual `api_price_missing -> api_price_missing` rows into the 30-day safety reprobe lane. The newest window still shows bounded `parked=` batches, including 28, 23, 16, 15, 14, 11, 8, 7, 6, 4, 3, and 2 rows. Those are newly maturing low-signal retries, not proof that the old saturated backlog returned. Complete fresh/material catalog evidence can still wake them immediately.

## PR #286 production acceptance — readable Product Lookup errors
PR #286 remains production-accepted.

The runtime repeatedly emits `decode=lookup_http_error_decode_v286` with readable Walmart code `6001` / `Results not found`. This does not prove the existing exact `ids` request is malformed. Do not switch endpoint or request semantics from this evidence alone. A later narrow normalization may map this specific no-result response to an existing exact-lookup no-match category without changing unrelated HTTP 400 handling.

## PR #287 production acceptance — HTTP 410 / response lifecycle
PR #287 is now live.

In the supplied post-merge production window from roughly 12:52 through 13:10:
- no new `page_http_410` outcome appears;
- no matching `ResourceWarning: unclosed <ssl.SSLSocket ...>` appears;
- Product Lookup and exact verification continue operating;
- therefore the intended 410-as-page-absence and HTTPError-close behavior is production-accepted for this observed window.

The remaining acceptance criterion is not met: the page-recovery due lane stays saturated at `>=64` throughout the window. Examples include:
- 12:53:04 — page recovery `>=64`;
- 13:03:17 — page recovery `>=64`;
- 13:08:31 — page recovery `>=64`.

Fresh/ordinary retry pressure rises and falls independently, and confirmed-no-price pressure often returns to single digits or zero after parking. The persistent floor is still page recovery.

## Current production finding
Do **not** raise page concurrency, claim caps, or weaken proof yet.

The current logs tell us that the page-recovery lane is large, but not why those rows are there. The batch result's `page **0**` counter only reports the current outcome of claimed work; it does not reveal the stored `last_error` distribution of the still-due `page_retry` population. A row can have entered the page lane earlier and later produce an API-only result, so that counter cannot be used as a backlog reason histogram.

The next structurally correct step is bounded observability of the due `page_retry` reasons.

## Implementation scope — PR #288
1. Preserve all current scheduling, claim priority, page pacing, challenge circuit, claim caps, dormancy, and exact proof behavior.
2. When the existing bounded pressure read proves page recovery is saturated at the full 64-row cap, sample at most 64 currently due `page_retry` rows through the existing `status/due` index.
3. Group only their stored `retry_kind=` values from `last_error`; do not read the full queue or change any row.
4. Emit `marker=page_recovery_reason_sample_v288` with sampled count, cap, saturation flag, and bounded reason counts.
5. Run the extra diagnostic read at most once every five minutes per process.
6. If the diagnostic query fails, log the diagnostic failure and continue normal exact work. Observability must never block the queue.
7. Do not normalize old reasons, reschedule rows, increase page traffic, or change Product Lookup semantics in this PR.

## Validation required for PR #288
- regression proving the reason sample is capped at 64;
- regression proving it uses `idx_walmart_exact_detail_queue_status_due`;
- regression proving it filters only due, retained, unleased `page_retry` rows;
- regression proving reason grouping and `unclassified` fallback;
- regression proving the five-minute diagnostic cadence;
- existing queue pressure, claim, pacing, challenge-circuit, #285 dormancy, #286 lookup diagnostics, and #287 page lifecycle tests;
- native libsql/process runtime tests;
- gateway #284 regression;
- compile/import/pip checks;
- full pytest suite;
- exact changed-file scope, diff, conflict-marker scan, clean worktree, and final SHA recheck.

## Production acceptance for PR #288
After deployment, capture one or more lines containing `marker=page_recovery_reason_sample_v288`.

Use the actual bounded reason distribution to choose the next behavior fix. In particular:
- a backlog dominated by `page_pacing_deferred` would point at scheduling/claim semantics rather than Walmart HTTP failures;
- a backlog dominated by challenge/403/429 classes would point at page availability/circuit behavior;
- a backlog dominated by legacy `page_http_410` would mean old rows still need to cycle or require a bounded compatibility cleanup;
- another dominant reason should be fixed narrowly from its own evidence.

No concurrency or trust-gate change is authorized until that distribution is visible.

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
Validate PR #288 on one exact head. Merge only after the bounded reason-sampling regressions, existing Walmart recovery/queue suites, full suite, compile/import/pip checks, exact scope/diff/conflict checks, clean worktree, and final SHA recheck all pass.
