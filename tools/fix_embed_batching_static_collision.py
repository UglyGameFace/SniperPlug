from pathlib import Path

path = Path("sniperplug/cogs/deal_scanner.py")
text = path.read_text()
updated = text.replace("cards=shown_cards,", "cards=list(shown_cards),")
if updated == text and "cards=list(shown_cards)," not in text:
    raise SystemExit("safe delivery card argument not found")
path.write_text(updated)
