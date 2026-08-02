# Deploy the standalone HP Store watcher

The HP watcher is a second continuously running Discloud application. It does **not** log in to Discord and does not post messages itself. It discovers and verifies HP Store prices, then writes exact deal events into the same Turso/libSQL database used by SniperPlug. The existing SniperPlug bot consumes those events and applies each server's threshold, categories, channel, duplicate protection, feedback controls, active-deal cache, and opt-in DM filters.

## Why it must be a separate application

The HP catalog watcher performs continuous sitemap discovery, product identity refreshes, structured price polling, price history, and parser-health checks. Keeping that work outside the Discord process prevents HP network delays from blocking commands or Walmart verification.

A separate Discloud application cannot share SniperPlug's local SQLite file. Both applications must use the same remote Turso/libSQL database in production.

## Files

Deploy the same repository, but use this configuration template for the second app:

```text
discloud.hp-watcher.config
hp_watcher_main.py
requirements.txt
sniperplug/
```

Before uploading the watcher app, copy `discloud.hp-watcher.config` to `discloud.config` in that deployment package. Do not replace the normal SniperPlug bot's `discloud.config` in its existing application.

The watcher template uses:

```env
NAME=SniperPlug-HP-Watcher
TYPE=bot
MAIN=hp_watcher_main.py
RAM=256
VERSION=3.11
AUTORESTART=true
BUILD=pip install -r requirements.txt
```

Discloud's `bot` application type is used for continuously running background services; the watcher still does not require a Discord bot token.

## Required environment variables

Set these in the **HP watcher application's** Discloud environment panel:

```env
TURSO_DATABASE_URL=the_exact_same_value_used_by_SniperPlug
TURSO_AUTH_TOKEN=the_exact_same_value_used_by_SniperPlug
```

Do not set a different database. The shared database is the integration contract between the two processes.

`DISCORD_TOKEN` is not required for the watcher.

## Recommended watcher settings

These defaults are already safe and bounded:

```env
HP_WATCHER_REQUIRE_REMOTE_DB=true
HP_WATCHER_LOOP_SECONDS=30
HP_SITEMAP_BATCH_SIZE=2
HP_PRODUCT_PAGE_BATCH_SIZE=12
HP_OFFER_BATCH_SIZE=40
HP_REQUEST_CONCURRENCY=3
HP_REQUEST_TIMEOUT_SECONDS=20
HP_MIN_EVENT_DISCOUNT_PERCENT=10
HP_NORMAL_OFFER_INTERVAL_MINUTES=30
HP_MARKDOWN_OFFER_INTERVAL_SECONDS=90
HP_PRODUCT_PAGE_REFRESH_HOURS=24
HP_SITEMAP_REFRESH_MINUTES=10
```

The watcher uses conditional sitemap requests, bounded concurrency, retries with backoff, and durable database scheduling. Increasing concurrency aggressively can make HP throttle or block requests and usually reduces reliability rather than improving it.

## What the watcher trusts

The watcher discovers official US HP product URLs from HP sitemaps, extracts the exact HP SKU/part number and catalog entry ID from the product page, then requests HP's structured `HPServices` price response.

A price is accepted only when the returned `productId` and normalized `partNumber` exactly match the claimed product. The watcher rejects:

- `$0.00` and other non-positive prices
- missing or malformed `priceData`
- cross-product or cross-SKU responses
- visible-page price text without structured proof
- unsupported non-HP URLs
- schema drift that removes required identity fields

HP's structured `lPrice` is labeled **HP MSRP**. It is not described as the normal market price. When HP does not provide an MSRP, a later price drop can use the prior exact HP.com observation as its reference.

## Existing SniperPlug servers

When the updated SniperPlug bot starts, enabled servers that already receive Walmart public alerts are enrolled in HP Store fanout once. Disabled destinations and custom non-Walmart retailer selections are not changed.

New `/setup_sniperplug_here` runs enable both Walmart and HP Store delivery. The watcher does not create one scan per server; it scans once globally and SniperPlug fans out verified events.

## Startup checks

Watcher logs should include:

```text
Standalone HP watcher starting backend=turso
HP watcher cycle: ...
```

SniperPlug logs should include:

```text
external_verified_event_fanout=true
verified retailer fanout: ...
```

The watcher writes coverage and health state to `hp_store_watcher_health`, catalog state to `hp_store_catalog_products`, exact offer state/history to `hp_store_offer_state` and `hp_store_offer_history`, and verified delivery events to `retailer_verified_deal_events`.

## Local one-cycle test

For a local development database only:

```env
HP_WATCHER_REQUIRE_REMOTE_DB=false
HP_WATCHER_RUN_ONCE=true
DATABASE_PATH=./data/sniperplug.sqlite3
```

Then run:

```text
python hp_watcher_main.py
```

Never use separate local SQLite files for the two production applications; SniperPlug would not see the watcher's events.
