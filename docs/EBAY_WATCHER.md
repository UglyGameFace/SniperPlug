# Standalone eBay big-ticket watcher

SniperPlug's eBay integration is an always-on worker, not a command-time search.

## What it watches

Two protected lanes are seeded on first start:

- **High-demand exact products** — fast query watches for products such as RTX 50-series GPUs, current consoles/handhelds, flagship iPhones, and MacBook Pro.
- **Broad big-ticket searches** — slower discovery for gaming laptops, workstations, graphics cards, cameras/lenses, luxury watches, unlocked phones, professional audio, and power-tool kits.

The owner can add, update, pause, resume, remove, or restore rules with `/ebay_watch`. Every rule can set its own query/category/seller/GTIN/ePID, condition list, minimum trusted reference value, scan interval, and verified discount threshold.

Defaults:

```env
EBAY_MIN_DISCOUNT_PERCENT=69
EBAY_BIG_TICKET_MIN_REFERENCE_PRICE=200
EBAY_SOUGHT_AFTER_MIN_REFERENCE_PRICE=75
EBAY_RULE_INTERVAL_SECONDS=300
EBAY_BIG_TICKET_RULE_INTERVAL_SECONDS=900
EBAY_TRACKED_INTERVAL_SECONDS=900
EBAY_BACKGROUND_TRACKED_INTERVAL_SECONDS=1800
EBAY_MINIMUM_COMPARABLES=5
EBAY_MINIMUM_BASELINE_OBSERVATIONS=2
EBAY_MINIMUM_BASELINE_AGE_SECONDS=240
EBAY_MIN_SELLER_FEEDBACK_PERCENTAGE=97
EBAY_MIN_SELLER_FEEDBACK_SCORE=10
```

## Verification policy

An alert is blocked unless all of these are true:

1. The listing is active and has a fixed Buy It Now price.
2. Item price plus shipping is known exactly in USD.
3. The eBay condition is normalized and allowed by the watch rule.
4. Seller identity, feedback percentage, and feedback score pass the rule.
5. Packaging-only, parts-only, broken, replica, and other suspicious listings are rejected.
6. The exact product fingerprint includes GTIN/ePID or brand plus MPN/model, with important variation aspects such as capacity, color, size, platform, edition, and pack quantity.
7. The reference is either:
   - SniperPlug's durable price history for that same eBay item, or
   - the median delivered price from at least five other exact-product, same-condition listings from distinct sellers.
8. Seller-supplied `marketingPrice.originalPrice` is never accepted as proof.
9. The discount passes the rule (69% by default), and the reference qualifies as big-ticket or the rule explicitly marks the product as highly sought after.
10. A second exact `getItem` request matches price, condition, seller, and fingerprint before the event enters SniperPlug's shared outbox.

Auctions are intentionally excluded from the first release because a current bid is not a reliable final purchase price.

## Required credentials

Create eBay Production application keys and set:

```env
EBAY_CLIENT_ID=your-production-client-id
EBAY_CLIENT_SECRET=your-production-client-secret
EBAY_ENVIRONMENT=production
EBAY_MARKETPLACE_ID=EBAY_US
EBAY_BUYER_COUNTRY=US
EBAY_BUYER_POSTAL_CODE=your-US-zip
```

The worker must use the same remote database as the Discord bot:

```env
TURSO_DATABASE_URL=the-same-value-used-by-SniperPlug
TURSO_AUTH_TOKEN=the-same-value-used-by-SniperPlug
```

The standalone worker does not need a Discord token. SniperPlug remains the only process that posts Discord alerts.

Deploy it as a second Discloud application using `discloud.ebay-watcher.config`.

## API behavior

- Browse `search` discovers newly listed fixed-price inventory.
- Browse `getItems` refreshes up to 20 already tracked exact items per call.
- Browse `getItem` independently confirms every qualifying alert.
- OAuth application tokens are cached and refreshed before expiration.
- 429 and transient 5xx responses use bounded retry/backoff.
- Search rules are leased durably so restarts do not create simultaneous duplicate scans.
- Listing observations and event keys are durable and duplicate-safe.

eBay's Feed API can later increase category-wide coverage, but the first working watcher does not depend on restricted Feed access.
