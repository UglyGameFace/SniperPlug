#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "🔧 Removing embed delivery startup patch"

cd ~/SniperPlug
git checkout main
git pull --ff-only origin main

python - <<'PY'
from pathlib import Path

# 1) Move should_split_embeds into native embed_delivery.py
p = Path("sniperplug/services/embed_delivery.py")
s = p.read_text(encoding="utf-8")

if "from collections.abc import Sequence" not in s:
    s = s.replace("from typing import Any\n", "from collections.abc import Sequence\nfrom typing import Any\n")

if "def should_split_embeds(" not in s:
    marker = "\ndef batch_embeds_for_limit("
    helper = '''
def should_split_embeds(embeds: Any) -> bool:
    if not embeds or not isinstance(embeds, Sequence):
        return False
    if len(embeds) <= 1:
        return False
    if not all(isinstance(embed, discord.Embed) for embed in embeds):
        return False
    total = sum(embed_text_size(embed) for embed in embeds)
    return total > SAFE_EMBED_MESSAGE_LIMIT

'''
    s = s.replace(marker, "\n" + helper + marker.lstrip("\n"))

p.write_text(s, encoding="utf-8")

# 2) Update tests to use native module and rename test file away from "patch"
old_test = Path("tests/test_embed_delivery_patch.py")
new_test = Path("tests/test_embed_delivery.py")
if old_test.exists():
    t = old_test.read_text(encoding="utf-8")
    t = t.replace(
        "from sniperplug.services.embed_delivery import SAFE_EMBED_MESSAGE_LIMIT, embed_text_size\n"
        "from sniperplug.services.embed_delivery_patch import should_split_embeds",
        "from sniperplug.services.embed_delivery import SAFE_EMBED_MESSAGE_LIMIT, embed_text_size, should_split_embeds",
    )
    new_test.write_text(t, encoding="utf-8")
    old_test.unlink()

# 3) Make verified hunt use native safe batching, not global send monkey patch
vh = Path("sniperplug/services/verified_discount_hunt.py")
s = vh.read_text(encoding="utf-8")

if "from sniperplug.services.embed_delivery import batch_cards_for_limit, sanitize_embed" not in s:
    s = s.replace(
        "from sniperplug.services.deal_finder_telemetry import",
        "from sniperplug.services.embed_delivery import batch_cards_for_limit, sanitize_embed\n"
        "from sniperplug.services.deal_finder_telemetry import",
    )

old = '''async def send_card_batches(interaction: discord.Interaction, *, summary: discord.Embed, cards: list[DealCard], review_cards: list[DealCard] | None = None) -> None:
    await interaction.followup.send(embed=summary, ephemeral=True)
    for batch in chunked(cards, 5):
        await interaction.followup.send(embeds=[card.embed for card in batch], view=deal_scanner.PresetResultView(batch), ephemeral=True)
    for batch in chunked(review_cards or [], 5):
        await interaction.followup.send(content="🟨 Review/flip/scout API leads — private only, not public-posted as verified deals.", embeds=[card.embed for card in batch], view=deal_scanner.PresetResultView(batch), ephemeral=True)
'''

new = '''async def send_card_batches(interaction: discord.Interaction, *, summary: discord.Embed, cards: list[DealCard], review_cards: list[DealCard] | None = None) -> None:
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

if old in s:
    s = s.replace(old, new)
else:
    print("⚠️ send_card_batches exact block not found; leaving it unchanged for manual review.")

vh.write_text(s, encoding="utf-8")

# 4) Remove boot-time monkey patch from bot.py
bot = Path("sniperplug/bot.py")
s = bot.read_text(encoding="utf-8")
s = s.replace("from sniperplug.services.embed_delivery_patch import install_safe_followup_send_patch\n", "")
s = s.replace(
    '        install_safe_followup_send_patch()\n'
    '        log.info("Discord embed sanitizer installed: followup_send=true")\n\n',
    "",
)
s = s.replace(
    'log.info("Runtime safety guards ready: embed_sanitizer=true provider_count=%s", len(provider_registry.providers))',
    'log.info("Runtime services ready: provider_count=%s", len(provider_registry.providers))',
)
bot.write_text(s, encoding="utf-8")

# 5) Delete old patch module
patch = Path("sniperplug/services/embed_delivery_patch.py")
if patch.exists():
    patch.unlink()

print("✅ Native embed delivery helper updated")
print("✅ Hunt sender uses native safe batching")
print("✅ Startup monkey patch removed from bot.py")
print("✅ Old embed_delivery_patch.py deleted")
PY

echo "🔎 Checking for leftover embed patch references..."
if grep -R "embed_delivery_patch\|install_safe_followup_send_patch" -n sniperplug tests; then
  echo "❌ Leftover embed patch reference found. Stop and inspect above."
  exit 1
fi

echo "🧪 Compile check..."
python -m compileall -q sniperplug

echo "🧪 Focused tests..."
python -m pytest -q \
  tests/test_embed_delivery.py \
  tests/test_verified_discount_hunt.py \
  tests/test_walmart_provider.py \
  tests/test_walmart_visible_savings_reference_guard.py

git status --short
git add -A
git commit -m "Remove embed delivery startup patch"
git push origin main

echo "✅ Done. Redeploy SniperPlug."
