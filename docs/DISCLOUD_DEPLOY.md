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
SNIPERPLUG_EMBED_TARGET_WATCHER=true
SNIPERPLUG_EMBED_HP_WATCHER=false
SNIPERPLUG_SQLITE_BACKUP_INTERVAL_HOURS=6
SNIPERPLUG_SQLITE_BACKUP_RETENTION=14
```

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
absent it selects the local SQLite implementation. Embedded HP/Target startup also
fails closed if a remote backend is active, preventing an accidental split-brain
migration.

## 3. Migrate the existing production data once

Do not start with an empty SQLite file unless losing the current guild settings,
queues, history, DM preferences and watcher state is intentional.

1. Stop the canonical SniperPlug app and the old standalone HP/Target watcher apps.
2. Export/download the current Turso database as a SQLite snapshot when Turso
   permits reads/export again.
3. Put that snapshot somewhere in the canonical Discloud app, for example
   `./migration/sniperplug-turso.sqlite3`.
4. With SniperPlug still stopped, run:

```bash
python scripts/install_sqlite_snapshot.py \
  ./migration/sniperplug-turso.sqlite3 \
  --target ./data/sniperplug.sqlite3
```

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
canonical runtime also creates an online, integrity-checked snapshot immediately
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
- `DATABASE_PATH=./data/sniperplug.sqlite3`;
- the migrated file exists;
- Target is enabled only when its API key is present;
- HP remains disabled until its current HTTP incident is repaired.

Then start/redeploy the canonical app.

Expected startup evidence includes:

```text
Database connected backend=sqlite
Database schema ready backend=sqlite
Local SQLite production mode active ... turso_required=false
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
