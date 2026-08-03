# Active Task

## Status
In progress — build a standalone eBay watcher for big-ticket and highly sought-after products, with verified customizable extreme-drop alerts.

## Scope
Add an always-on eBay worker that uses the official eBay Browse API for listing discovery and exact listing data, stores its own observation history, recognizes listing condition, prioritizes big-ticket and high-demand products, and publishes verified candidates through SniperPlug's existing shared retailer-event outbox and Discord delivery controls.

Default policy:
- Minimum verified discount: **69%**
- Big-ticket reference/value floor: **$200**
- Highly sought-after items may qualify below $200 when explicitly matched by configured demand rules.
- Fixed-price listings are enabled by default; auctions remain opt-in until auction-reference logic is independently proven.

## Root cause and execution-path findings
- eBay does not provide a ready-made price-drop watcher; Browse API provides current structured listing data while SniperPlug must retain historical prices and prove the drop.
- Search results include condition, seller, price, buying options, product identifiers, item aspects, and shipping data, but an eBay marketing/original price is not consistently available or sufficient proof by itself.
- A broad "scan all eBay" loop is impossible under ordinary Browse API call limits; watches must be query/category/GTIN/ePID/seller based and scheduled fairly.
- eBay Feed/Hourly Snapshot can later widen coverage, but production access is restricted and cannot be required for the first working watcher.
- The current verified-retailer fanout is HP-only, so eBay events require retailer-neutral routing and an eBay-specific deal card/post gate without weakening HP behavior.

## Planned changes
- Add bounded eBay OAuth/Browse API client with application-token caching and retry/backoff.
- Add durable eBay watch, listing, observation, and health tables.
- Add default high-demand watch seeds plus configurable keyword/category/GTIN/ePID/seller rules.
- Normalize eBay condition IDs/names into SniperPlug conditions and fail closed when condition is unclear or excluded.
- Build exact listing fingerprints using item ID, product identifiers, aspects, quantity, seller, and condition.
- Detect listing price drops from SniperPlug's own prior observations and separately identify newly listed below-market candidates only when a trusted comparable baseline exists.
- Prioritize big-ticket and sought-after queues while preserving fair background coverage and API budgets.
- Publish verified 69%+ candidates through the existing retailer outbox with duplicate-safe event keys.
- Add eBay card/public fanout support and health visibility.
- Add a separate Discloud worker entrypoint/config sharing SniperPlug's Turso database and requiring no Discord token.

## Validation required
- Compile all changed Python files.
- Run import smoke checks for SniperPlug and the standalone eBay watcher.
- Run targeted OAuth/client, parser, condition, fingerprint, scheduling, history, qualification, and fanout tests.
- Run the complete pytest suite.
- Inspect conflicts, review threads, temporary files, duplicate logic, and obsolete retailer-specific branches before merge.

## Cleanup status
Not started.

## Blockers
- Live production monitoring requires eBay Production Client ID/Client Secret and Buy API approval. The implementation must remain testable with fixtures and Sandbox-compatible OAuth behavior.

## Backlog
- Add Feed API/Hourly Snapshot ingestion after eBay grants production feed access.
- Consider auction-ending alerts only after a separate comparable-price and bid-state policy is validated.
