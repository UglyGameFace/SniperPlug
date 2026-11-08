#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "🧹 Cleaning duplicate SniperPlug commands and stale logic"
cd ~/SniperPlug

git checkout main
git pull --ff-only origin main

python - <<'PY'
from pathlib import Path
import re

def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")

# ------------------------------------------------------------
# 1) Remove old /sniperplug setup/status/providers duplicates
# ------------------------------------------------------------
p = Path("sniperplug/cogs/sniperplug.py")
s = p.read_text(encoding="utf-8")

s, count_setup = re.subn(
    r'\n    @app_commands\.command\(name="setup".*?\n(?=    @app_commands\.command\(name="set_channel")',
    "\n",
    s,
    flags=re.S,
)

s, count_status = re.subn(
    r'\n    @app_commands\.command\(name="status".*?\n(?=    @app_commands\.command\(name="providers")',
    "\n",
    s,
    flags=re.S,
)

s, count_providers = re.subn(
    r'\n    @app_commands\.command\(name="providers".*?\n(?=    @app_commands\.command\(name="provider_scan")',
    "\n",
    s,
    flags=re.S,
)

s = s.replace("    @setup.error\n", "")
s = s.replace("    @providers.error\n", "")

write("sniperplug/cogs/sniperplug.py", s)

print(f"✅ Removed old /sniperplug setup blocks: {count_setup}")
print(f"✅ Removed old /sniperplug status blocks: {count_status}")
print(f"✅ Removed old /sniperplug providers blocks: {count_providers}")

if count_setup != 1 or count_status != 1 or count_providers != 1:
    raise SystemExit("Expected to remove exactly one setup/status/providers block. Inspect sniperplug/cogs/sniperplug.py.")

# ------------------------------------------------------------
# 2) Remove duplicate /autoscan_setup
# ------------------------------------------------------------
p = Path("sniperplug/cogs/public_alerts.py")
s = p.read_text(encoding="utf-8")

s, count_auto = re.subn(
    r'\n    @app_commands\.command\(name="autoscan_setup".*?\n(?=    @app_commands\.command\(name="public_alerts")',
    "\n",
    s,
    flags=re.S,
)

s, count_embed = re.subn(
    r'\n\ndef autoscan_setup_complete_embed\(.*?\n(?=\ndef public_alert_status_embed)',
    "\n",
    s,
    flags=re.S,
)

s = s.replace(
    "This is the simple view. Use `/public_alerts` to update posting, `/autoscan_setup` for Walmart one-shot setup, `/deal_threshold` to adjust markdown, and `/autoscan_health` to diagnose posting.",
    "This is the simple view. Use `/setup_sniperplug_here` for one-step setup, `/public_alerts` to fine-tune posting, `/deal_threshold` to adjust markdown, and `/autoscan_health` to diagnose posting.",
)

s = s.replace(
    'return "⛔ No public channel saved. Run `/autoscan_setup channel:#walmart-deals`."',
    'return "⛔ No public channel saved. Run `/setup_sniperplug_here` inside the channel you want, or `/setup_sniperplug channel:#walmart-deals`."',
)

# Add permission check before /public_alerts saves a bad channel.
needle = '''        if enabled and channel_id is None:
            await interaction.followup.send("Public alerts need a channel. Re-run this with `channel:#your-deals-channel` or run it inside the channel you want to use.", ephemeral=True)
            return

        await set_public_alert_config(
'''
replacement = '''        if enabled and channel_id is None:
            await interaction.followup.send("Public alerts need a channel. Re-run this with `channel:#your-deals-channel` or run it inside the channel you want to use.", ephemeral=True)
            return
        if enabled and chosen_channel is not None and interaction.guild is not None:
            missing = public_alert_channel_missing_permissions(chosen_channel, interaction.guild.me)
            if missing:
                await interaction.followup.send(public_alert_channel_missing_permissions_message(chosen_channel, missing), ephemeral=True)
                return

        await set_public_alert_config(
'''

if needle in s:
    s = s.replace(needle, replacement)
else:
    print("⚠️ Could not insert /public_alerts permission guard automatically.")

# Add helper functions if not present.
helper_marker = "\ndef public_alert_status_embed("
helpers = '''
def public_alert_channel_missing_permissions(channel: discord.TextChannel, member: discord.Member | None) -> list[str]:
    if member is None:
        return []
    perms = channel.permissions_for(member)
    missing: list[str] = []
    if not getattr(perms, "view_channel", False):
        missing.append("View Channel")
    if not getattr(perms, "send_messages", False):
        missing.append("Send Messages")
    if not getattr(perms, "embed_links", False):
        missing.append("Embed Links")
    if not getattr(perms, "read_message_history", False):
        missing.append("Read Message History")
    return missing


def public_alert_channel_missing_permissions_message(channel: discord.TextChannel, missing: list[str]) -> str:
    return (
        f"SniperPlug cannot post in {channel.mention} yet.\\n\\n"
        "Missing channel permissions:\\n"
        + "\\n".join(f"• {perm}" for perm in missing)
        + "\\n\\nGive the SniperPlug bot/role those permissions, then run `/setup_sniperplug_here` in that channel."
    )

'''
if "def public_alert_channel_missing_permissions(" not in s:
    s = s.replace(helper_marker, "\n" + helpers + helper_marker.lstrip("\n"))

# Make autoscan health check Read Message History too.
s = s.replace(
    '''        if not getattr(perms, "embed_links", True):
            missing.append("Embed Links")
        if missing:
''',
    '''        if not getattr(perms, "embed_links", True):
            missing.append("Embed Links")
        if not getattr(perms, "read_message_history", True):
            missing.append("Read Message History")
        if missing:
''',
)

write("sniperplug/cogs/public_alerts.py", s)

print(f"✅ Removed /autoscan_setup command blocks: {count_auto}")
print(f"✅ Removed /autoscan_setup helper blocks: {count_embed}")

if count_auto != 1:
    raise SystemExit("Expected to remove exactly one /autoscan_setup command block. Inspect sniperplug/cogs/public_alerts.py.")

# ------------------------------------------------------------
# 3) Fix Doctor stale monkey-patch check
# ------------------------------------------------------------
p = Path("sniperplug/cogs/settings_dashboard.py")
s = p.read_text(encoding="utf-8")

if "from sniperplug.services import embed_delivery as embed_delivery_module\n" not in s:
    s = s.replace(
        "from sniperplug.providers.registry import provider_registry\n",
        "from sniperplug.providers.registry import provider_registry\n"
        "from sniperplug.services import embed_delivery as embed_delivery_module\n",
    )

s = s.replace(
    'checks.append(check("Embed sanitizer", bool(getattr(discord.Webhook.send, "_sniperplug_safe_followup_send_installed", False)), "followup sends protected from field/total limits"))',
    'checks.append(check("Native embed delivery", hasattr(embed_delivery_module, "sanitize_embed") and hasattr(embed_delivery_module, "batch_embeds_for_limit"), "safe embed sizing lives in native send helpers; no boot monkey patch required"))',
)

write("sniperplug/cogs/settings_dashboard.py", s)

print("✅ Fixed /sniperplug_doctor native embed delivery check")

# ------------------------------------------------------------
# 4) Update command catalog to remove duplicate/legacy commands
# ------------------------------------------------------------
p = Path("sniperplug/services/command_catalog.py")
s = p.read_text(encoding="utf-8")

def remove_catalog_entry(text: str, command_name: str) -> str:
    pattern = (
        r'\n    CommandCatalogEntry\(\n'
        r'\s+name="' + re.escape(command_name) + r'".*?'
        r'\n    \),'
    )
    text, count = re.subn(pattern, "", text, flags=re.S)
    print(f"✅ Catalog removed {command_name}: {count}")
    return text

for name in (
    "/autoscan_setup",
    "/sniperplug setup",
    "/sniperplug status",
    "/sniperplug providers",
):
    s = remove_catalog_entry(s, name)

s = s.replace(
    "Use after deploys or setup changes to confirm whether deals post, duplicate, cache, fail confidence, or fail channel/config gates.",
    "Use after deploys or setup changes to confirm whether deals post, duplicate, cache, fail confidence, or fail channel/config gates. Setup first with `/setup_sniperplug_here`.",
)

write("sniperplug/services/command_catalog.py", s)

# ------------------------------------------------------------
# 5) Update command catalog tests and add static cleanup test
# ------------------------------------------------------------
p = Path("tests/test_command_catalog.py")
s = p.read_text(encoding="utf-8")

if "def test_command_catalog_excludes_removed_duplicate_commands()" not in s:
    s += '''

def test_command_catalog_excludes_removed_duplicate_commands():
    names = {entry.name for entry in COMMAND_CATALOG}

    assert "/autoscan_setup" not in names
    assert "/sniperplug setup" not in names
    assert "/sniperplug status" not in names
    assert "/sniperplug providers" not in names
'''

write("tests/test_command_catalog.py", s)

write(
    "tests/test_command_surface_cleanup.py",
    '''from pathlib import Path


def test_removed_duplicate_commands_are_not_registered_in_source():
    sniperplug = Path("sniperplug/cogs/sniperplug.py").read_text(encoding="utf-8")
    public_alerts = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")

    assert 'name="setup"' not in sniperplug
    assert 'name="status"' not in sniperplug
    assert 'name="providers"' not in sniperplug
    assert 'name="autoscan_setup"' not in public_alerts


def test_doctor_no_longer_depends_on_embed_monkey_patch():
    dashboard = Path("sniperplug/cogs/settings_dashboard.py").read_text(encoding="utf-8")

    assert "_sniperplug_safe_followup_send_installed" not in dashboard
    assert "Native embed delivery" in dashboard
'''
)

print("✅ Updated command catalog tests")
print("✅ Added static duplicate-command cleanup test")
PY

echo "🔎 Checking for removed command references..."
if grep -R --exclude='*.pyc' --exclude-dir='__pycache__' \
  "name=\"autoscan_setup\"\|name=\"setup\"\|name=\"status\"\|name=\"providers\"\|/autoscan_setup\|/sniperplug setup\|/sniperplug status\|/sniperplug providers\|_sniperplug_safe_followup_send_installed" \
  sniperplug tests; then
  echo "❌ Found leftover removed-command or stale-patch reference. Inspect above."
  exit 1
fi

echo "🧪 Compile check..."
python -m compileall -q sniperplug

echo "🧪 Focused tests..."
python -m pytest -q \
  tests/test_command_catalog.py \
  tests/test_command_surface_cleanup.py \
  tests/test_static_regressions.py \
  tests/test_libsql_connection_lock.py \
  tests/test_walmart_provider.py \
  tests/test_walmart_visible_savings_reference_guard.py

echo "📋 Git status:"
git status --short

git add -A
git commit -m "Clean duplicate SniperPlug commands"
git push origin main

echo "✅ Done. Redeploy SniperPlug."
