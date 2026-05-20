# SniperPlug Discord Bot Starter

SniperPlug is a Discord-first deal alert bot for online deals, price glitches, possible price errors, and YMMV offers.

This starter intentionally includes **only real Discord bot features**:
- `/sniperplug setup` — choose the Discord channel for deal alerts
- `/sniperplug status` — check current setup
- `/sniperplug test_alert` — post a realistic SniperPlug test alert
- Deal alert embeds with:
  - possible price glitch / error labels
  - Amazon YMMV warning
  - seller / condition risk flags
  - View Deal, Save, and Report Dead buttons
- SQLite storage for:
  - guild settings
  - deals
  - saved deals
  - dead deal reports

No fake retailer scraping code is included yet. Provider/API modules should be added only when the real API contract is chosen.

## Quick start

### 1. Create your Discord bot

In the Discord Developer Portal:
1. Create an application.
2. Add a bot.
3. Copy the bot token.
4. Invite the bot to your server with:
   - `applications.commands`
   - `bot`
   - Send Messages
   - Embed Links
   - Read Message History

### 2. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Configure

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Fill in:

```env
DISCORD_TOKEN=your_bot_token_here
DATABASE_PATH=./data/sniperplug.sqlite3
DEV_GUILD_ID=
```

`DEV_GUILD_ID` is optional. If you set it to your server ID, slash commands sync faster while testing.

### 4. Run

```bash
python main.py
```

### 5. In Discord

Run:

```text
/sniperplug setup channel:#deals
/sniperplug test_alert
```

## What comes next

Next files to add when we connect real APIs:

```text
sniperplug/providers/keepa_provider.py
sniperplug/providers/rainforest_provider.py
sniperplug/services/deal_normalizer.py
```

Do not add retailer modules until the API actually supports the fields we need.
## Discloud + GitHub

This starter is now ready for both:

```text
GitHub source control
Discloud bot hosting
```

Important files:

```text
discloud.config
.gitignore
.github/workflows/python-check.yml
docs/DISCLOUD_DEPLOY.md
docs/GITHUB_SETUP.md
```

Do **not** commit `.env` or your Discord bot token.

For Discloud, set these environment variables in the Discloud dashboard:

```env
DISCORD_TOKEN=your_bot_token
DATABASE_PATH=./data/sniperplug.sqlite3
DEV_GUILD_ID=
```

See:

```text
docs/DISCLOUD_DEPLOY.md
docs/GITHUB_SETUP.md
```
