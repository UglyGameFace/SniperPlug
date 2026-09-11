# Active Task

## Status
**SP-WM-EXACT-RECOVERY-DEBT — IN PROGRESS.**

The user authorized moving here once `SP-GATEWAY-EVENT-LOOP-LAG` was solved. PR #284 remains production-accepted. The active lock remains Walmart exact-recovery debt until the remaining queue/recovery failure classes are resolved from production evidence.

Base main for the current follow-up: `7a25db0500c563992306ebec8a90db877233be46` (PR #286 merge).

## Completed prerequisite — SP-GATEWAY-EVENT-LOOP-LAG
PR #284 is production-accepted.

A real ~41-second Discord heartbeat warning can still occur while local loop lag remains under one second and the watchdog classifies it `gateway_ack_latency_loop_healthy`. Do not reopen or throttle Walmart based solely on those ACK/network-path events while local event-loop telemetry remains healthy.

## PR #285 production acceptance — confirmed no-price debt
PR #285 is live and its dormancy policy is working as intended.

Production moved the mature dual `api_price_missing -> api_price_missing` population out of the immediate retry lane in bounded passes. The bounded pressure view progressed from `confirmed no-price retry >=600`, to single digits, and then to `confirmed no-price retry 0`. Later `parked=6/7/8` lines are newly maturing rows being moved to the 30-day safety reprobe, not the old saturated backlog returning.

This is the intentional workaround for Walmart exact responses that contain no canonical purchase price: preserve fail-closed proof, stop repeatedly hammering unchanged no-price rows, and allow complete fresh/material catalog evidence to wake them immediately.

## PR #286 production acceptance — readable Product Lookup errors
PR #286 is live and accepted for its intended transport fix.

Production now emits `decode=lookup_http_error_decode_v286`, and the formerly compressed HTTP 400 body is readable. Walmart's actual response is code `6001` with message `Results not found` for the exact requested item ID.

That evidence does **not** show a malformed `ids` request shape. It shows a per-item no-result response from the Affiliate Product Lookup endpoint. Do not switch endpoint or request semantics based on this response. A later narrow normalization may map this specific 6001 no-result case to the existing exact-lookup no-match category, while unrelated 400 validation errors remain `api_http_400`.

## Current production bottleneck
After the no-price lane reached zero, bounded queue pressure still bottoms out at:

- new/ordinary retry: `0` in the cleanest sample;
- confirmed no-price retry: `0`;
- page recovery: `>=64`;
- scheduled rechecks: `0`.

That isolates page recovery as the current dominant due backlog.

The same production window also produced `retry_kind=page_http_410` followed seconds later by `ResourceWarning: unclosed <ssl.SSLSocket ...>`. Inspection of `_request_product_page_scripts` confirms both behaviors are real code issues:

1. `urllib.error.HTTPError` is not deterministically closed on the page-recovery path.
2. HTTP 410 Gone is treated as a transient `page_retry`, even though HTTP 404 already means the HTML fallback has no product resource.

A 410 from the optional HTML identity fallback cannot create exact proof by being retried every 30 minutes. The official API remains authoritative. Treating 410 as page absence preserves fail-closed behavior while allowing strict fresh catalog proof to rearm the item later if valid evidence returns.

## Implementation scope — PR #287
1. Preserve the official API-first exact flow, page challenge circuit, one-probe pacing, and page claim cap.
2. Close every page-recovery `HTTPError` response in a `finally` path so 404/410 returns and transient raises cannot leak the response socket.
3. Treat HTTP 410 Gone the same as the existing HTTP 404 absence case: return no structured scripts rather than creating another transient `page_retry`.
4. Preserve transient retry behavior for 403/408/425/429/5xx and other non-absence HTTP failures.
5. Preserve exact item, seller, selected-offer, variant, marketplace, shipping, price, and reference proof rules unchanged.
6. Preserve strict fresh-snapshot rescue of incomplete/page-recovery rows. No guessed seller/offer data and no weaker promotion path.
7. Do not raise page concurrency or page claim caps merely to force-drain the backlog.

## Validation required for PR #287
- regression proving HTTP 410 returns page absence and closes its response body;
- regression proving existing HTTP 404 absence also closes its response body;
- regression proving a true transient HTTP error such as 503 still raises `page_http_503` and closes its response body;
- regression proving an absent page produces no fabricated identity proof;
- existing Walmart page identity/recovery tests;
- exact queue/pacing/circuit regressions;
- #285 dormancy and #286 exact-lookup diagnostics regressions;
- native libsql/process runtime tests;
- gateway #284 regression;
- compile/import/pip checks;
- full pytest suite;
- exact changed-file scope, diff, conflict-marker scan, clean worktree, and final SHA recheck.

## Production acceptance for PR #287
After deployment:

- no new `page_http_410` retry should be created for a 410 Gone page;
- the matching page HTTPError path must no longer emit an unclosed-SSL `ResourceWarning`;
- the `page recovery >=64` lane should begin dropping as old due rows are naturally reclaimed and reclassified;
- if page recovery remains saturated after 410 rows cycle out, inspect the bounded reason distribution before changing pacing or concurrency.

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
Validate PR #287 on one exact head. Do not merge until the new page HTTP lifecycle regressions, existing Walmart recovery/queue suites, full suite, compile/import/pip checks, exact scope/diff/conflict checks, clean worktree, and final SHA recheck all pass.
