from pathlib import Path

path = Path("sniperplug/cogs/auto_scan_runner.py")
text = path.read_text()
old = "Auto-scan guild run failed but other guild tasks will continue guild=%s"
new = "Auto-scan guild run failed but loop will continue; other guild tasks are isolated guild=%s"
if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit("autoscan resilience log message not found")
path.write_text(text)
