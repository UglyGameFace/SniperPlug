from pathlib import Path

path = Path("tests/test_walmart_page_identity_recovery.py")
path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")
