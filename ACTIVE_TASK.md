# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: recover exact current/reference prices when Walmart supplies valid same-item data, keep every item/seller/offer/variant/shipping guard fail-closed, drain the actionable exact queue normally, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue claim → fresh proof/API detail → exact-ID Affiliate Product Lookup recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**PR #274 is merged and its production deployment is proven. The newly isolated blocker is Product Lookup exact items that still expose no parser-recognized current price. PR #275 is active and diagnostic-only.**

PR #274 validated head: `30a82f7c6e83e520d199d3008729907bb5ddd3f1`
PR #274 merge: `d7c466851d956cffa336224d218e6f3339c2008e`
PR #270 validated head: `5e48002743fdae819d147fa7b79b21fc288d5ba1`
PR #270 merge: `33e82775cd06aef46f023e91da0f5a6177e2cbad`
#257 merge: `4d7646dd0b4d120c4c994d60b3d78b6c08760c53`

## Production evidence after #270
The Sep 9 production process repeatedly emitted:

`price_recovery=exact_id_search_v257`

so the recovery-capable runtime is definitely deployed. The old deployment ambiguity is closed.

The paired exact summaries then consistently reported:

`api_price_missing->api_exact_search_no_match`

for essentially every stale/no-snapshot exact claim, commonly 8/8 and 24/24 at a time. Page failures were zero in those batches and the page circuit was closed, so HTML/page pacing is not the immediate blocker.

The exact queue remained under heavy pressure, repeatedly reaching approximately 121-128 actionable rows and pausing catalog discovery through fresh-work backpressure.

Fresh official catalog proof itself is still healthy. Examples from the same process:
- one catalog batch discovered 89 items with 28 safe fresh proofs, followed by an exact batch verifying 23/24;
- another discovered 98 items with 14 safe fresh proofs, followed by a batch verifying 13/24.

Those fresh candidates still mostly lacked a trusted Walmart reference price, so they produced no public markdown cards, but they prove Walmart authentication, catalog discovery, selected-offer parsing, exact fresh proof, and persistence are not globally broken.

## Proven root cause fixed by #274
The failing #257 recovery used the Affiliate **Search** endpoint with the Walmart item ID placed in the free-text `query` parameter. It then correctly required one search result to have the exact requested `itemId`/`usItemId`. Production proved that this relevance-search operation overwhelmingly returned no exact-ID row, which became `api_exact_search_no_match`.

The provider already exposed the Affiliate Product Lookup `/items` service and its base detail code included an `ids=<item_id>` form. However, the ordinary detail path could return the first successful identity payload even when that payload had no numeric current price, so the later #257 recovery still fell through to free-text Search instead of performing a dedicated exact Product Lookup.

## PR #274 implementation
### `sniperplug/providers/walmart_exact_lookup.py`
- adds one bounded Affiliate Product Lookup request using `ids=<exact Walmart item id>` and `responseGroup=full`;
- uses the provider's existing signed `_request_json` transport and publisher ID when configured;
- accepts only a returned `itemId`/`usItemId` that exactly equals the requested ID;
- searches only known Product Lookup wrapper shapes with bounded depth/node count;
- tags accepted payloads as `affiliate_product_lookup_exact_item` / `items_ids_v274`;
- fails closed with concrete `api_exact_lookup_*` retry kinds.

### `sniperplug/services/walmart_exact_fetch_pacing.py`
- keeps the canonical exact verifier as the only promotion path;
- on `api_price_missing`, uses exact Product Lookup instead of numeric free-text Search when the production provider supports the real lookup transport;
- uses the same lookup for best-effort trusted-reference enrichment of already-valid fresh proof;
- preserves the old exact-search hook only as a compatibility adapter for tests/plugins that do not expose the production Product Lookup transport;
- does not add HTML calls or relax any proof gate.

### `sniperplug/services/walmart_exact_queue_bulk_runtime.py`
- preserves the #270 deployment marker;
- adds `exact_lookup=affiliate_items_ids_v274` to write telemetry so production can prove #274 is running.

### `tests/test_walmart_exact_product_lookup_274.py`
- verifies exact Product Lookup uses `ids`, not `query`;
- verifies a nonmatching returned item is rejected;
- verifies bounded known-wrapper exact-ID extraction;
- verifies an identity-only primary detail can recover price/reference through Product Lookup without free-text Search or HTML.

## Production acceptance after #274
The Sep 9 deployment emitted both:

`price_recovery=exact_id_search_v257`

and:

`exact_lookup=affiliate_items_ids_v274`

so the merged #274 path is definitely live.

The old dominant failure changed from:

`api_price_missing->api_exact_search_no_match`

to:

`api_price_missing->api_price_missing`

for most stale/no-snapshot claims. That proves #274 successfully removed relevance Search as the production exact-price recovery bottleneck.

One live exact batch also recovered a full trusted markdown:
- claimed 24;
- verified 1;
- official was prices 1;
- markdowns 1;
- JLab Go Air Sport at `$24.99`, 21.88% off.

The normal fanout path then checked both eligible guilds and correctly suppressed the deal because 21.88% was below their configured 50% and 40% thresholds. That proves exact Product Lookup can recover current/reference proof and that canonical verification, durable event creation, fanout, and threshold filtering remain functional.

The remaining production pattern is still poor:
- most 8/2 and 24/4 batches verify 0 rows;
- the dominant outcome is now `api_price_missing->api_price_missing`;
- occasional `api_http_400` responses occur but are a minority;
- actionable queue pressure repeatedly returns to approximately 128;
- catalog discovery is repeatedly paused by fresh exact-detail backpressure.

## Newly isolated blocker after #274
The remaining unknown is now narrow and must be proven before another behavioral fix:

1. Walmart Product Lookup may be returning the exact requested item with a valid current price in a response path the canonical parser does not recognize; or
2. the exact requested item may genuinely contain no usable numeric current price in the Affiliate Product Lookup response.

The existing parser already recognizes the established Walmart current-price forms including `salePrice`, `sale_price`, `currentPrice`, `current_price`, `price`, `priceInfo.currentPrice`, and price-map variants. Therefore adding more guessed price aliases is not justified without live payload-shape evidence.

## PR #275 implementation — bounded diagnostics only
Branch: `fix/walmart-exact-price-diagnostics-275`

### `sniperplug/providers/walmart_exact_lookup.py`
- keeps exact Product Lookup and exact-ID matching unchanged;
- adds bounded `lookup_shape_v275` diagnostics only when a successfully matched exact Product Lookup item has no current price recognized by the existing canonical Walmart price parser;
- records item field names, price-like field paths with compact scalar value/type information, and selected-offer container key shapes;
- never dumps the product title or full payload;
- caps traversal depth, nodes, fields, offer shapes, and final diagnostic length;
- rate-limits missing-price diagnostics to one sample per minute per provider process;
- adds a separately rate-limited truncated diagnostic for the minority `api_http_400` exact-lookup failures;
- does not choose a price, modify a candidate, change retry policy, add another fetch, or weaken any proof gate.

### `tests/test_walmart_exact_price_diagnostics_275.py`
- proves an unrecognized nested price path appears in the bounded shape summary;
- proves product titles are not emitted into the diagnostic;
- proves repeated price-missing lookups are rate-limited to one log sample;
- proves an already-recognized `salePrice` does not emit a missing-price diagnostic;
- proves HTTP 400 diagnostics are rate-limited while the original exception is still re-raised.

## Safety invariants
Unchanged:
- exact Walmart item identity is mandatory;
- search/relevance neighbors cannot donate price, seller, reference, or offer proof;
- seller, selected offer, variant, marketplace, condition, fulfillment, shipping/delivered-price checks remain fail-closed;
- no catalog price is promoted merely because detail omitted a price;
- reference-price trust policy and discount threshold are unchanged;
- queue cadence, retry delays, page challenge circuit, HTML pacing, dedupe, categories, fanout, and DM behavior are unchanged;
- no second verifier and no guessed price source are introduced.

## Validation / results
PR #274 passed in the known-good Termux/Ubuntu Python 3.12.14 environment:
- targeted Walmart Product Lookup / #257 recovery / page identity / queue / bulk / fresh-proof regressions: **62 passed, 1 existing `audioop` deprecation warning**;
- `pip check`: **No broken requirements found**;
- compileall: **exit 0**;
- import smoke: **37 modules, 22 symbols**;
- native libsql process tests: **10 passed**;
- full pytest: **1,385 passed, 1 existing warning**;
- `git diff origin/main...HEAD --check`: clean;
- changed-file scope exactly the five intended files;
- conflict-marker scan: none;
- validation worktree clean;
- final exact HEAD recheck matched the validated SHA.

PR #275 validation is pending on its exact head. Do not mark it complete or merge it until targeted tests, compile/import checks, full regression coverage, diff/scope checks, and exact-head validation pass.

## Production acceptance after #275
After a validated #275 deployment, look for:

`marker=lookup_shape_v275`

A missing-price line must show a bounded `shape=` summary for the exact matched item. Use that evidence to decide the next change:
- if a plausible current price is present at a previously unsupported field path, add only that proven path to canonical normalization with regression coverage;
- if the exact item contains no usable current price, do not add another parser alias or search fallback. Instead change only the retry/scheduling treatment for this proven no-price population so it cannot keep the hot queue pinned and starve discovery.

The minority HTTP 400 diagnostic should reveal the truncated upstream reason before any 400-specific behavior is changed.

## Blockers / risks
- ChatGPT has repository access but no Discloud deployment/runtime connector, so live deployment and production-log acceptance remain external.
- Walmart Affiliate response shapes are external and may vary; all #275 diagnostics are intentionally bounded and observational.
- Two production `discord.gateway Can't keep up` warnings were also observed while the Walmart queue was hot. That remains the separate backlog task `SP-GATEWAY-EVENT-LOOP-LAG` and is not being mixed into this task.

## Suspended task — SP-MOVIE-TICKETS-RECOVERY
Suspended by the user's explicit FORCE SWITCH.

Preserved state:
- #265, #266, #267 merged and validated;
- #268 validated and merged at `e970b2dcb5854da06c25c9a9e9b42476975584fa`;
- PR #271 merged Gofobo truthfulness at `60cc3f8b55d2543cfa396105f189ec2427462ca7`;
- PR #272 merged movie result embed-budget protection at `fe0558ea7795d773e0f17e52f8284153711a3b36`;
- remaining movie step is production acceptance on the existing July 31 panel;
- unrelated draft PR #273 is left untouched while Walmart remains the active task;
- movie work must resume from its preserved acceptance state, not from reimplementation.

## Backlog — not active
- SP-GATEWAY-EVENT-LOOP-LAG
- SP-SETUP-DELIVERY-UX
- SP-DM-DELIVERY-STARVATION
- SP-DM-NEW-LOW-ALERT
- SP-WM-CASH-PDP-AMOUNT
- eBay watcher PR #200
- HP/Target standalone watcher deployment/acceptance
- SniperPlug-Site work

## Next step
Validate PR #275 on its exact head. If green, deploy it to the existing Discloud SniperPlug app and use the first `lookup_shape_v275` missing-price/HTTP-400 diagnostics to prove the exact downstream field shape before making any behavioral change.
