# Active Task

## Task / outcome
**SP-WM-DEAL-DELIVERY-STALL — restore live Walmart exact verification and verified-deal delivery without weakening proof gates.**

Target outcome: recover exact current/reference prices when Walmart supplies valid same-item data, keep every item/seller/offer/variant/shipping guard fail-closed, drain the actionable exact queue normally, and deliver qualifying verified deals through the existing fanout path.

## Scope
`deployed-version proof → catalog discovery → exact queue claim → fresh proof/API detail → exact-ID Affiliate Product Lookup recovery → selected-offer/current/reference normalization → exact verification → durable fanout → public/DM delivery`

Do not switch to movie tickets, gateway lag, setup UX, DM redesign, watchers, site, Minecraft, or unrelated work without another explicit FORCE SWITCH.

## Status
**PR #270 production acceptance proved the deployed recovery build and exposed the remaining failing stage. PR #274 is now the active implementation branch.**

Active branch: `fix/walmart-exact-product-lookup-274`
Branch base/main: `7efb68c60b9cbe1c703e6bb2734b107f2f24626e`
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

## Proven root cause
The failing #257 recovery uses the Affiliate **Search** endpoint with the Walmart item ID placed in the free-text `query` parameter. It then correctly requires one search result to have the exact requested `itemId`/`usItemId`. Production now proves that this relevance-search operation overwhelmingly returns no exact-ID row, which becomes `api_exact_search_no_match`.

The provider already exposes the Affiliate Product Lookup `/items` service and its base detail code includes an `ids=<item_id>` form. However, the ordinary detail path returns the first successful identity payload even when that payload has no numeric current price, so the later #257 recovery still falls through to free-text Search instead of performing a dedicated exact Product Lookup.

That is the stage to fix. Adding more page fallbacks, relaxing identity checks, or treating a relevance neighbor as proof would be the wrong layer.

## PR #274 implementation
### `sniperplug/providers/walmart_exact_lookup.py`
- add one bounded Affiliate Product Lookup request using `ids=<exact Walmart item id>` and `responseGroup=full`;
- use the provider's existing signed `_request_json` transport and publisher ID when configured;
- accept only a returned `itemId`/`usItemId` that exactly equals the requested ID;
- search only known Product Lookup wrapper shapes with bounded depth/node count;
- tag accepted payloads as `affiliate_product_lookup_exact_item` / `items_ids_v274`;
- fail closed with concrete `api_exact_lookup_*` retry kinds.

### `sniperplug/services/walmart_exact_fetch_pacing.py`
- keep the canonical exact verifier as the only promotion path;
- on `api_price_missing`, use exact Product Lookup instead of numeric free-text Search when the production provider supports the real lookup transport;
- use the same lookup for best-effort trusted-reference enrichment of already-valid fresh proof;
- preserve the old exact-search hook only as a compatibility adapter for tests/plugins that do not expose the production Product Lookup transport;
- do not add HTML calls or relax any proof gate.

### `sniperplug/services/walmart_exact_queue_bulk_runtime.py`
- preserve the #270 deployment marker;
- add `exact_lookup=affiliate_items_ids_v274` to write telemetry so production can prove #274 is running.

### `tests/test_walmart_exact_product_lookup_274.py`
- verify exact Product Lookup uses `ids`, not `query`;
- verify a nonmatching returned item is rejected;
- verify bounded known-wrapper exact-ID extraction;
- verify an identity-only primary detail can recover price/reference through Product Lookup without free-text Search or HTML.

## Safety invariants
Unchanged:
- exact Walmart item identity is mandatory;
- search/relevance neighbors cannot donate price, seller, reference, or offer proof;
- seller, selected offer, variant, marketplace, condition, fulfillment, shipping/delivered-price checks remain fail-closed;
- no catalog price is promoted merely because detail omitted a price;
- reference-price trust policy and discount threshold are unchanged;
- queue cadence, retry delays, page challenge circuit, HTML pacing, dedupe, categories, fanout, and DM behavior are unchanged;
- no second verifier and no guessed price source are introduced.

## Validation
### Completed foundation
PR #270 exact head `5e48002743fdae819d147fa7b79b21fc288d5ba1` passed:
- targeted Walmart regressions;
- pip check;
- compileall;
- import smoke;
- native libsql process tests;
- full pytest **1,380 passed, 1 existing warning**;
- diff/scope/conflict/clean-worktree/final-SHA checks.

### Required for PR #274
Do not mark ready or merge until the exact PR #274 head passes:
- new Product Lookup regression file;
- existing #257 recovery tests;
- Walmart page-identity, exact/bulk, fresh-proof, queue/drain, and price-proof regressions;
- `pip check`;
- compileall;
- import smoke;
- native libsql process tests;
- full pytest;
- diff check;
- changed-file scope check;
- conflict-marker scan;
- clean validation worktree;
- final exact-SHA recheck.

## Production acceptance after #274
Deploy the merged canonical `main`. A claimed Walmart exact batch must contain:

`exact_lookup=affiliate_items_ids_v274`

Then inspect outcomes:
- the old dominant `api_price_missing->api_exact_search_no_match` should disappear from the production provider path;
- a successful Product Lookup recovery should increase stale/no-snapshot verified counts and may recover trusted references when Walmart returns them;
- an exact Product Lookup response with no requested item must fail as `api_exact_lookup_no_match`;
- an exact item that still has no usable current price must remain fail-closed as a price-missing retry;
- queue pressure should begin draining rather than pinning near its cap;
- any public markdown still has to pass the existing canonical proof/fanout path.

Only a newly proven downstream blocker may justify another implementation change.

## Blockers / risks
- ChatGPT has repository access but no Discloud deployment/runtime connector, so live deployment and production-log acceptance remain external.
- Walmart Affiliate response shapes are external and may vary; #274 intentionally keeps exact wrapper traversal bounded and exact-ID matched.
- Two production `discord.gateway Can't keep up` warnings were also observed while the Walmart queue was hot. That remains the separate backlog task `SP-GATEWAY-EVENT-LOOP-LAG` and is not being mixed into this PR.

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
Open draft PR #274 from `fix/walmart-exact-product-lookup-274`, inspect its exact diff, run exact-head validation in the known-good Termux/Ubuntu Python 3.12 environment, and merge only after that gate is green.