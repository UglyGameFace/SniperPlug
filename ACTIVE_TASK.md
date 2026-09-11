# Active Task

## Status
**SP-WM-EXACT-RECOVERY-DEBT — IN PROGRESS.**

The user authorized moving here once `SP-GATEWAY-EVENT-LOOP-LAG` was solved. PR #284 is production-accepted. PR #285 is merged and its dormancy mechanism is now live in production. The active lock remains Walmart exact-recovery debt while the smaller recovery failure classes are resolved from production evidence.

Base main for the current follow-up: `1b9f0ecdc6768ac866e72672cb79655617c77340` (PR #285 merge).

## Completed prerequisite — SP-GATEWAY-EVENT-LOOP-LAG
PR #284 is production-accepted.

A real 41.5-second Discord heartbeat warning was correlated with only ~0.29s local event-loop lag and then recovered normally. The residual event is Discord heartbeat ACK/network-path latency, not SniperPlug event-loop starvation. Do not throttle Walmart or reopen that task based solely on another `Can't keep up` warning while local loop-lag telemetry remains healthy.

## PR #285 production evidence
The deployed runtime proves the confirmed-no-price dormancy pass is active:

- `marker=confirmed_no_price_dormancy_v285` is present;
- each pass is parking exactly the configured bounded batch of 200 rows;
- write telemetry carries `confirmed_no_price_dormancy=confirmed_no_price_dormancy_v285`;
- fresh exact work continues to verify normally, including a 24/24 batch with 20 official price results and later fresh-proof batches;
- the queue still reports `confirmed no-price retries >=600`, but that field is deliberately capped at 600 and therefore only proves that more than 600 due rows remain. It does not show that the same parked rows are being reclaimed.

The supplied post-deploy window contains more than twenty successful 200-row parking passes, so thousands of mature low-signal rows are being moved out of the immediate six-hour retry lane. Continue to treat the `>=600` display as a saturated lower bound until the underlying population falls below the cap.

## Current production symptom
With #285 live, the next concrete issue is now visible instead of being buried under the no-price wave:

- Product Lookup still occasionally returns HTTP 400;
- the current diagnostic text is binary-looking garbage rather than Walmart's validation response;
- immediately after one such HTTP 400, Python emitted `ResourceWarning: unclosed <ssl.SSLSocket ...>`;
- the ordinary exact verifier remains healthy around those incidents, so this is transport/error-body handling, not a broad API outage;
- a rare `page_http_410` also remains and will be handled separately after the Product Lookup failure is made readable.

## Root-cause finding for PR #286
`sniperplug/providers/walmart_exact_lookup.py` delegates the real Affiliate Product Lookup request to the provider's generic `_request_json` transport. Walmart's legacy Affiliate endpoint can return a compressed HTTP error entity. The current HTTP-error path reads those bytes and decodes them directly as UTF-8, which explains the production diagnostic garbage. The HTTPError response object is also not deterministically closed after its body is read, matching the observed unclosed SSL socket warning.

The next patch must not guess at a new Walmart request shape yet. First make the existing exact Product Lookup transport surface the actual upstream validation response and close the HTTP error response reliably.

## Implementation scope — PR #286
1. Keep the exact Product Lookup endpoint, `ids` parameter, signature headers, timeout, exact-item verification, and canonical price proof unchanged.
2. Give the real Product Lookup path a narrow transport wrapper that still uses the provider's signed headers.
3. Decode HTTP error bodies according to `Content-Encoding`, including gzip/deflate, before constructing `WalmartProviderError`.
4. Always close the HTTPError response after reading it so compressed 400s cannot leak SSL socket wrappers.
5. Preserve the existing generic `_request_json` fallback for lightweight test doubles that do not expose signed headers.
6. Add `lookup_http_error_decode_v286` to the Product Lookup HTTP 400 diagnostic so production can prove the new path is live.
7. Do not alter retry policy, price/reference trust, seller/offer/variant/marketplace/shipping proof, fanout, page recovery, or the #285 dormancy logic.

## Validation required for PR #286
- new gzip HTTP 400 decode/close regression;
- exact `ids` request and exact-item handoff regression;
- prior #274-#276 Product Lookup/diagnostic/handoff regressions;
- #277-#285 Walmart recovery/queue regressions;
- native libsql/process runtime tests;
- gateway #284 regression;
- compile/import/pip checks;
- full pytest suite;
- diff/scope/conflict-marker/clean-worktree checks on one exact head;
- production acceptance showing `decode=lookup_http_error_decode_v286` and a readable Walmart HTTP 400 validation body instead of compressed garbage, with no matching unclosed-SSL ResourceWarning.

## Follow-up inside this active task
Once the readable upstream 400 response identifies Walmart's actual validation complaint, change request semantics only if that evidence requires it. Existing public examples show both query-parameter and path-item forms have existed for the legacy Affiliate Product API, but that is not enough to blindly switch production behavior before seeing the live error body.

After the Product Lookup failure class is resolved, inspect the remaining `page_http_410` / page-recovery population separately. A rare page error alone is not evidence for weakening the existing one-probe pacing or page challenge circuit.

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
Validate PR #286 on one exact head. Merge only after the targeted Product Lookup transport tests, Walmart regressions, full suite, exact scope, diff/conflict checks, and final SHA recheck all pass. Then deploy and capture the next live Product Lookup HTTP 400 so its actual upstream validation message can drive the semantic fix instead of another guess.
