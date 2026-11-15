# SniperPlug real operations dashboard

**Source of truth:** the canonical Discloud SniperPlug app and its local SQLite database. The Cloudflare Pages dashboard is a read-only owner view of sanitized reports. Neither a GitHub Actions workflow nor a site deployment proves the production bot is running.

## What it actually measures

Every five minutes (when paired), the canonical bot issues bounded SELECT queries against its existing tables:
- `walmart_exact_detail_queue`: exact-item status totals, due work, newest retained verified/error items and prices with provenance distinctions.
- `walmart_worker_cycle_health`: exact/catalog success, failures, timeouts, missing-price counts.
- `walmart_global_catalog_autoscan_state`: completed routes, passes and last completion.
- `walmart_global_exact_deal_events`: verified fanout events and errors in the last 24 hours.
- `guild_public_deal_posts`: actual per-server public posts, summed across guilds for the last 24 hours. Counts represent messages, not unique items.
- `walmart_guild_fanout_decisions`: historical posts, duplicates, muted categories, below-threshold and quality-gate decisions.

No SELECT requests use raw Discord messages, user identifiers, full error strings, private seller metadata, or API credentials. Only a limited sample of exact Walmart product IDs, names, approved prices and timestamps is transmitted. The data may be incomplete due to retention, source omissions, collector failures or the bot not being deployed.

**Still not instrumented:** independent source offers missed before discovery, measured per-query ~16-minute revisit distribution, per-item/per-guild alert receipts, current retailer checkout/stock, jewelry authenticity confirmation and matched completed-sales net resale ROI. The dashboard must display unknown for these. It cannot guarantee a deal was missed or profitable.

## Safely connect from Android

1. Back up the **canonical** Discloud SniperPlug application, including `data/sniperplug.sqlite3`, to off-host storage before any redeploy. Never start with an empty DB.
2. Open `https://sniperplug.com/operations/`, sign in using the existing owner Control Center account, and choose **Generate private connection key**. This saves only a SHA-256-derived HMAC key to Cloudflare D1 and displays the one-time plaintext key once.
3. In the *canonical* SniperPlug Discloud app's environment, set `SNIPERPLUG_OPS_INGEST_SECRET` to that one-time key. Do **not** add it to GitHub, an issue, Discord or public logs.
4. Deploy only the integrated bot revision from `main` after the backup and migration-safety checks; leave all existing SQLite, quota and watcher settings unchanged. If available, set `SNIPERPLUG_RELEASE_SHA` to the exact deployed bot commit.
5. Check the dashboard for an actual signed report. The first receipt proves a recent data path, **not** the correctness of each deal or live checkout. Verify one item against /sniperplug deal_trace and actual retailer timestamps.

The collector starts only if the key is present and at least 32 characters. It does not execute, restart, post or schedule new scans. It is canceled with the bot and has no retry loop beyond the normal next five-minute report. Any telemetry outage leaves normal bot behavior unchanged.

## Authorization and retention

Requests are signed with an HMAC-SHA256 derived from SHA-256(one-time pairing token), timestamp and raw JSON bytes. Cloudflare accepts a maximum 64 KB body and requires timestamp skew within five minutes. Each snapshot has a unique UUID, and duplicate IDs are ignored. The website's POST ingestion endpoint accepts **only** telemetry, while owner-only read endpoints require the existing Control Center session. 14-day data retention and bounded 96-row history are independent of SniperPlug's local database.

The pairing key never appears in a read response. The **Rotate** button requires explicit confirmation and invalidates the previous bot key until it is replaced in Discloud. The dashboard's views do not modify SniperPlug settings or retailer APIs.

## Release evidence

- Code/tests merged on GitHub is separate from Discloud deployment and DB integrity.
- Cloudflare D1 migration `0008_sniperplug_operations.sql` is additive and must be applied to the production `SNIPERPLUG_DB` before owner pairing can succeed.
- Validate the exact deployed bot SHA, real first signed receipt, worker-cycle metrics, a stored decision and a corresponding actual Discord receipt before marking the live feed accepted.
