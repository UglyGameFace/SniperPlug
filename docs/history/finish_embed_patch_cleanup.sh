#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "🧹 Finishing embed patch cleanup"
cd ~/SniperPlug

echo "🧽 Removing Python cache files..."
find . -type d -name "__pycache__" -prune -exec rm -rf {} +

python - <<'PY'
from pathlib import Path
import re

# 1) Remove stale import from static regression test.
p = Path("tests/test_static_regressions.py")
s = p.read_text(encoding="utf-8")

s = s.replace("    import sniperplug.services.embed_delivery_patch  # noqa: F401\n", "")

# Add the removed patch module/hook to the regression deny-list.
if '"embed_delivery_patch.py",' not in s:
    s = s.replace(
        '    "walmart_marketplace_comp_guard.py",\n}',
        '    "walmart_marketplace_comp_guard.py",\n    "embed_delivery_patch.py",\n}',
    )

if '"install_safe_followup_send_patch",' not in s:
    s = s.replace(
        '    "install_strict_walmart_cash_guard",\n}',
        '    "install_strict_walmart_cash_guard",\n    "install_safe_followup_send_patch",\n}',
    )

p.write_text(s, encoding="utf-8")

# 2) Ensure verified hunt uses native embed delivery batching.
vh = Path("sniperplug/services/verified_discount_hunt.py")
s = vh.read_text(encoding="utf-8")

if "from sniperplug.services.embed_delivery import batch_cards_for_limit, sanitize_embed" not in s:
    s = s.replace(
        "from sniperplug.models.candidate import SourceCandidate\n",
        "from sniperplug.models.candidate import SourceCandidate\n"
        "from sniperplug.services.embed_delivery import batch_cards_for_limit, sanitize_embed\n",
    )

replacement = '''async def send_card_batches(interaction: discord.Interaction, *, summary: discord.Embed, cards: list[DealCard], review_cards: list[DealCard] | None = None) -> None:
    await interaction.followup.send(embed=sanitize_embed(summary), ephemeral=True)
    for batch in batch_cards_for_limit(cards):
        await interaction.followup.send(
            embeds=[sanitize_embed(card.embed) for card in batch],
            view=deal_scanner.PresetResultView(batch),
            ephemeral=True,
        )
    for batch in batch_cards_for_limit(review_cards or []):
        await interaction.followup.send(
            content="🟨 Review/flip/scout API leads — private only, not public-posted as verified deals.",
            embeds=[sanitize_embed(card.embed) for card in batch],
            view=deal_scanner.PresetResultView(batch),
            ephemeral=True,
        )
'''

s, count = re.subn(
    r"async def send_card_batches\(.*?\n(?=def merge_review_and_scout_cards)",
    replacement + "\n",
    s,
    flags=re.S,
)

if count != 1:
    raise SystemExit(f"Expected to replace exactly one send_card_batches function, replaced {count}")

vh.write_text(s, encoding="utf-8")

print("✅ Removed stale static test import")
print("✅ Added embed patch to removed-module regression list")
print("✅ Verified hunt sender now uses native safe batching")
PY

echo "🔎 Checking source code for leftover startup patch references..."
if grep -R --exclude='*.pyc' --exclude-dir='__pycache__' "embed_delivery_patch\|install_safe_followup_send_patch" -n sniperplug; then
  echo "❌ Leftover source reference found. Stop and inspect above."
  exit 1
fi

echo "🧪 Compile check..."
python -m compileall -q sniperplug

echo "🧪 Focused tests..."
python -m pytest -q \
  tests/test_embed_delivery.py \
  tests/test_static_regressions.py \
  tests/test_verified_discount_hunt.py \
  tests/test_walmart_provider.py \
  tests/test_walmart_visible_savings_reference_guard.py

echo "📋 Git status:"
git status --short

git add -A

if git diff --cached --quiet; then
  echo "✅ No changes to commit."
else
  git commit -m "Finish embed delivery startup patch removal"
  git push origin main
  echo "🚀 Pushed cleanup."
fi

echo "✅ Done. Redeploy SniperPlug."
