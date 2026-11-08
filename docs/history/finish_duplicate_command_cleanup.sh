#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "🧹 Finishing duplicate-command cleanup without false grep hits"
cd ~/SniperPlug

# Do not pull here because you may have uncommitted cleanup changes from the last script.
git fetch origin main || true

python - <<'PY'
from pathlib import Path

# 1) Replace leftover user-facing /autoscan_setup mentions in source only.
replacements = {
    "/autoscan_setup channel:#your-channel": "/setup_sniperplug_here",
    "/autoscan_setup channel:#walmart-deals": "/setup_sniperplug_here",
    "/autoscan_setup channel:#deals": "/setup_sniperplug_here",
    "/autoscan_setup": "/setup_sniperplug_here",
    "safety guards": "safety checks",
    "Safety guards": "Safety checks",
}

for path in Path("sniperplug").rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    new = text
    for old, repl in replacements.items():
        new = new.replace(old, repl)
    if new != text:
        path.write_text(new, encoding="utf-8")
        print(f"✅ Updated references in {path}")

# 2) Tight sanity checks that only target the removed SniperPlug duplicates.
sniperplug = Path("sniperplug/cogs/sniperplug.py").read_text(encoding="utf-8")
public_alerts = Path("sniperplug/cogs/public_alerts.py").read_text(encoding="utf-8")
dashboard = Path("sniperplug/cogs/settings_dashboard.py").read_text(encoding="utf-8")
catalog = Path("sniperplug/services/command_catalog.py").read_text(encoding="utf-8")

bad = []

for marker in (
    '@app_commands.command(name="setup"',
    '@app_commands.command(name="status"',
    '@app_commands.command(name="providers"',
):
    if marker in sniperplug:
        bad.append(f"Removed SniperPlug group command still exists in sniperplug.py: {marker}")

if '@app_commands.command(name="autoscan_setup"' in public_alerts:
    bad.append("Removed /autoscan_setup command still exists in public_alerts.py")

for marker in (
    "/autoscan_setup",
    "/sniperplug setup",
    "/sniperplug status",
    "/sniperplug providers",
):
    if marker in catalog:
        bad.append(f"Removed command still exists in command catalog: {marker}")

if "_sniperplug_safe_followup_send_installed" in dashboard:
    bad.append("Doctor still checks old embed monkey patch flag")

if "embed_delivery_patch" in dashboard:
    bad.append("Doctor still references deleted embed_delivery_patch module")

if bad:
    print("❌ Cleanup sanity failed:")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("✅ Removed SniperPlug duplicate commands are gone")
print("✅ Verizon Shine setup/status ignored correctly because they are separate feature commands")
print("✅ Test assertions ignored correctly")
PY

echo "🔎 Source-only check for real leftovers..."
if grep -R --exclude='*.pyc' --exclude-dir='__pycache__' "/autoscan_setup\|_sniperplug_safe_followup_send_installed\|embed_delivery_patch" -n sniperplug; then
  echo "❌ Real source leftover found. Inspect above."
  exit 1
fi

echo "🔎 Exact duplicate command registration check..."
if grep -n '@app_commands.command(name="setup"\|@app_commands.command(name="status"\|@app_commands.command(name="providers"' sniperplug/cogs/sniperplug.py; then
  echo "❌ Old /sniperplug group setup/status/providers still registered."
  exit 1
fi

if grep -n '@app_commands.command(name="autoscan_setup"' sniperplug/cogs/public_alerts.py; then
  echo "❌ /autoscan_setup still registered."
  exit 1
fi

echo "🧪 Compile check..."
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
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

if git diff --cached --quiet; then
  echo "✅ No changes to commit."
else
  git commit -m "Finish duplicate command cleanup"
  git push origin main
  echo "🚀 Pushed command cleanup."
fi

echo "✅ Done. Redeploy SniperPlug."
