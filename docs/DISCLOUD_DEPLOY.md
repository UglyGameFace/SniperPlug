# Deploy SniperPlug on Discloud

## 1. Files Discloud needs

Keep these files at the root of the project:

```text
main.py
requirements.txt
discloud.config
sniperplug/
```

Do **not** upload:

```text
.env
.venv/
venv/
__pycache__/
data/
```

## 2. Discloud config

This starter includes:

```env
NAME=SniperPlug
TYPE=bot
MAIN=main.py
RAM=512
VERSION=latest
AUTORESTART=true
BUILD=pip install -r requirements.txt
```

`AUTORESTART=true` keeps the bot restarting after crashes on supported Discloud plans.

## 3. Environment variables

Set these inside Discloud's environment variable panel, not inside GitHub:

```env
DISCORD_TOKEN=your_discord_bot_token
DATABASE_PATH=./data/sniperplug.sqlite3
DEV_GUILD_ID=
```

For faster slash-command testing, set:

```env
DEV_GUILD_ID=your_test_server_id
```

For production, leave `DEV_GUILD_ID` blank so commands sync globally.

## 4. Upload/deploy

Use Discloud's GitHub deployment flow or upload the project zip.

After deploy, check logs for:

```text
SniperPlug online as ...
Synced ... slash commands
```

## 5. Discord setup

Inside your server:

```text
/sniperplug setup channel:#deals
/sniperplug test_alert
```

## 6. Invite permissions

Use these scopes:

```text
bot
applications.commands
```

Recommended bot permissions for this starter:

```text
View Channels
Send Messages
Embed Links
Read Message History
```

You do **not** need Administrator for the public invite.

## 7. Storage warning

SQLite is fine for the first Discloud test.

Before SniperPlug becomes a real paid/public bot, move storage to Turso, Supabase Postgres, or another hosted database so saved deals, guild settings, and dead reports survive production redeploys cleanly.
