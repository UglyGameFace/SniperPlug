# Deploy SniperPlug on Discloud Diamond

SniperPlug's canonical production layout is one Discloud bot application with one
local SQLite database. Optional HP and Target watchers run as supervised tasks in
that same application and open separate SQLite connections to the same WAL-backed
file. SniperPlug remains the only Discord publisher.

This layout removes the paid/limited remote database dependency while preserving
one authoritative data set.

## 1. Canonical production app

`discloud.config` is the only production app definition required for the local
SQLite layout:

```env
NAME=SniperPlug
TYPE=bot
MAIN=main.py
RAM=1024
VERSION=3.11
AUTORESTART=true
BUILD=pip install -r requirements.txt
```

The old `discloud.hp-watcher.config` and `discloud.target-watcher.config` files are
legacy standalone deployment definitions. **Do not run those apps at the same time
as embedded local watchers.** Separate Discloud applications have separate local
filesystems and cannot share this SQLite database.

## 2. Production environment

Keep secrets in Discloud's environment panel, never in GitHub.

Required base settings:

```env
DISCORD_TOKEN=your_discord_bot_token
DATABASE_PATH=./data/sniperplug.sqlite3
SNIPERPLUG_LOCAL_SQLITE=true
SNIPERPLUG_LOCAL_SQLITE_ALLOW_EMPTY=false
SNIPERPLUG_EMBED_TARGET_WATCHER=true
SNIPERPLUG_EMBED_HP_WATCHER=false
SNIPERPLUG_SQLITE_BACKUP_INTERVAL_HOURS=6
SNIPERPLUG_SQLITE_BACKUP_RETENTION=14
```

`SNIPERPLUG_LOCAL_SQLITE=true` is a safety assertion, not a secret alternate
storage implementation. The existing database factory still owns SQLite/Turso
selection. Local production mode refuses to start if any Turso/libSQL credential
is still present, so a stale secret cannot silently put one embedded watcher back
on the remote database. It also refuses to create a missing or empty production
SQLite file. `SNIPERPLUG_LOCAL_SQLITE_ALLOW_EMPTY=true` exists only as an explicit
one-time escape hatch for a deliberately fresh database; leave it `false` for the
migration and remove the override immediately after any intentional fresh boot.

Target additionally requires:

```env
TARGET_REDSKY_API_KEY=your_target_redsky_key
```

HP support is integrated into the same app, but leave
`SNIPERPLUG_EMBED_HP_WATCHER=false` until the separate HP transport/PDP timeout
incident is resolved. Turning it on before that would only restart an unhealthy
scanner on the local database.

### Remove the Turso variables

After the production snapshot has been installed locally, remove all four remote
libSQL variables from the canonical app:

```text
TURSO_DATABASE_URL
TURSO_AUTH_TOKEN
LIBSQL_URL
LIBSQL_AUTH_TOKEN
```

This is important. The existing database factory intentionally preserves backwards
compatibility: if Turso credentials are present it selects Turso; when they are
absent it selects the local SQLite implementation. Explicit local mode and embedded
watcher startup both fail closed when remote credentials are present, preventing an
accidental split-brain migration.

## 3. Migrate the existing production data once

Do not start with an empty SQLite file unless losing the current guild settings,
queues, history, DM preferences and watcher state is intentional. Local production
mode now fails closed before SQLite can create a missing/empty file unless the
one-time `SNIPERPLUG_LOCAL_SQLITE_ALLOW_EMPTY=true` override is explicitly set.

1. Create/download a fresh Discloud app backup before changing storage.
2. Stop the old standalone HP/Target watcher apps so they stop consuming Turso.
3. Obtain a current production SQLite snapshot using either route below.
4. Install the verified snapshot into the canonical local database path.
5. Only after the snapshot is installed, remove Turso/libSQL credentials and
   enable explicit local mode.

### Route A — provider export works

If Turso allows a normal SQLite export/download, place it somewhere in the
canonical Discloud app, for example:

```text
./migration/sniperplug-turso.sqlite3
```

### Route B — Turso UI is quota/storage locked but reads still work

Keep the current Turso/libSQL environment variables in place temporarily and run:

```bash
python scripts/export_turso_snapshot.py \
  --output ./migration/sniperplug-turso-export.sqlite3
```

The recovery exporter:

- enforces a local remote-SQL allowlist so only `SELECT`, `BEGIN`, rollback, and the read-only `PRAGMA table_info(...)` metadata query can reach Turso; this avoids Hrana's rejection of `PRAGMA query_only=ON` while still blocking remote writes before execution;
- starts a read transaction so the copied tables come from one consistent
  remote snapshot;
- reads schema and rows in bounded batches without issuing remote
  `INSERT`, `UPDATE`, `DELETE`, or migration statements;
- uses rowid keyset pagination where safe and an offset fallback for
  `WITHOUT ROWID` tables;
- recreates tables, indexes, triggers, and views locally;
- verifies required SniperPlug production tables;
- runs `PRAGMA integrity_check` and `PRAGMA foreign_key_check`;
- publishes the completed SQLite file only after verification passes;
- writes a SHA-256 manifest beside the export.

If Turso blocks reads during the copy, the command exits non-zero and leaves only
a `.partial` file. **Never install a `.partial` export.**

Then install whichever completed snapshot route produced:

```bash
python scripts/install_sqlite_snapshot.py \
  ./migration/sniperplug-turso-export.sqlite3 \
  --target ./data/sniperplug.sqlite3
```

For Route A, replace the source argument with the provider-exported filename.

The installer:

- runs `PRAGMA integrity_check` on the source;
- verifies core SniperPlug tables are present;
- preserves any existing local DB as a timestamped pre-migration backup;
- stages the new file instead of writing over the live path in place;
- removes stale WAL/SHM sidecars;
- atomically installs the snapshot;
- runs a second integrity check on the installed database.

Do not run the installer while SniperPlug is writing to the target database.

## 4. Local SQLite durability

The normal database layer already enables SQLite WAL mode and foreign keys. The
canonical runtime gives the bot and embedded watcher connections a bounded SQLite
busy timeout so ordinary overlapping writes wait rather than immediately failing
with `database is locked`.

The canonical runtime also creates an online, integrity-checked snapshot immediately
at startup and then every six hours by default under:

```text
data/backups/sniperplug-YYYYMMDDTHHMMSSZ.sqlite3
```

Only the newest 14 snapshots are retained by default. These are corruption/rollback
snapshots on the same Discloud app filesystem, not an off-host backup.

For off-host recovery, create a **Discloud app backup before every production
redeploy or migration** and download/store it outside the app. A code deployment
must never be treated as the only copy of `data/sniperplug.sqlite3`.

## 5. First local production boot

Before starting, verify:

- canonical SniperPlug is the only app that owns the SQLite file;
- old HP and Target standalone apps are stopped;
- the four Turso/libSQL variables are absent;
- `SNIPERPLUG_LOCAL_SQLITE=true`;
- `SNIPERPLUG_LOCAL_SQLITE_ALLOW_EMPTY=false` (or unset);
- `DATABASE_PATH=./data/sniperplug.sqlite3`;
- the migrated file exists;
- Target is enabled only when its API key is present;
- HP remains disabled until its current HTTP incident is repaired.

Then start/redeploy the canonical app.

Expected startup evidence includes:

```text
Database connected backend=sqlite
Database schema ready backend=sqlite
Local SQLite production mode active ... explicit_mode=true ... turso_required=false
Embedded retailer watchers configured backend=sqlite hp=false target=true separate_discloud_watcher_apps_required=false
Local SQLite backup verified ...
SniperPlug online as ...
```

If logs report a Turso backend, stop immediately and remove the leftover Turso or
LIBSQL environment variables before continuing.

## 6. Production acceptance

Do not call the migration accepted from one successful boot. Check all of the
following on the live canonical app:

- guild/server setup still resolves to the expected channels;
- DM preferences still exist;
- Walmart exact queue/history tables contain the migrated state;
- `/autoscan_health` loads without database errors;
- Target location profiles still resolve and its embedded watcher stays alive;
- verified-retailer outbox processing still reaches the main Discord fanout;
- a scheduled local backup is created and passes integrity validation;
- no old standalone watcher is writing to an isolated database;
- Turso usage no longer increases from SniperPlug.

## 7. Redeploy discipline

The runtime DB is state, not source code. Do not commit or package the live
`data/` directory into GitHub.

Before a code redeploy:

1. create/download a Discloud app backup;
2. confirm a recent `data/backups/*.sqlite3` snapshot exists;
3. deploy the code;
4. verify `backend=sqlite` and database counts/health after restart;
5. restore from the Discloud backup if the runtime filesystem did not retain the
   expected database.

This procedure is deliberately conservative because losing a 1 GB production
SQLite file to a cheerful one-click redeploy would be an exceptionally stupid way
to save five dollars a month.

## eBay research and promotional consent (same Discloud app)

The canonical `/ebay` group (registered in `sniperplug/bot.py`) performs **user-initiated official Browse searches only**. It does not run another Discloud app or depend on Turso; eBay opt-in/opt-out and admin-selected channel permission are stored in the same local SQLite file as SniperPlug.

Place `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` in the existing Discloud app's **secrets/environment**, never GitHub. Set `EBAY_RESEARCH_ENABLED=true` only after Production keys are verified. Use `/ebay status`, `/ebay consent`, `/ebay research` and `/ebay review_sample` to verify live API behavior and opt-in/opt-out evidence. Users and server admins can revoke their own explicit eBay permissions; neither consent setting automatically sends eBay messages.

**Approval boundary:** `EBAY_EPN_MESSAGING_APPROVED=false` stays disabled until written eBay Partner Network approval actually arrives. `EBAY_EPN_CAMPAIGN_ID` is optional until then. There is no separate eBay broadcast worker. Prior standalone watcher PR #200 was retired because it assumed a separate Discloud app sharing a remote Turso database, incompatible with the canonical local SQLite architecture. Do not deploy that PR.

**Review evidence:** capture *real* bot command screenshots after deployment: `/ebay consent` opt-in, `/ebay consent` opt-out, and `/ebay review_sample` on a successfully fetched real listing. The review preview explicitly says it is not a sent promotion. Do not represent it as an already-delivered marketing campaign or invent a sent example to meet EPN's support-email request. Supply real proof and explain the planned post-approval delivery path.
