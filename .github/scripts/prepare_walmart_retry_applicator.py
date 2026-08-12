from pathlib import Path

path = Path(__file__).with_name("apply_walmart_retry_fanout.py")
text = path.read_text(encoding="utf-8")
old = '''def replace_once(relative: str, old: str, new: str) -> None:\n    path = ROOT / relative\n    text = path.read_text(encoding="utf-8")\n    count = text.count(old)\n    if count != 1:\n        raise RuntimeError(f"{relative}: expected one replacement, found {count}: {old[:120]!r}")\n    path.write_text(text.replace(old, new, 1), encoding="utf-8")\n'''
new = '''def replace_once(relative: str, old: str, new: str) -> None:\n    path = ROOT / relative\n    text = path.read_text(encoding="utf-8")\n    count = text.count(old)\n    if count < 1:\n        raise RuntimeError(f"{relative}: replacement target missing: {old[:120]!r}")\n    path.write_text(text.replace(old, new, 1), encoding="utf-8")\n'''
if old not in text:
    raise RuntimeError("replace_once helper shape changed")
text = text.replace(old, new, 1)
old = '''def append_once(relative: str, marker: str, addition: str) -> None:\n    path = ROOT / relative\n    text = path.read_text(encoding="utf-8")\n    if addition.strip() in text:\n        return\n    count = text.count(marker)\n    if count != 1:\n        raise RuntimeError(f"{relative}: expected one append marker, found {count}")\n    path.write_text(text.replace(marker, marker + addition, 1), encoding="utf-8")\n'''
new = '''def append_once(relative: str, marker: str, addition: str) -> None:\n    path = ROOT / relative\n    text = path.read_text(encoding="utf-8")\n    if not addition.strip() or addition.strip() in text:\n        return\n    if marker not in text:\n        raise RuntimeError(f"{relative}: append marker missing")\n    path.write_text(text.replace(marker, marker + addition, 1), encoding="utf-8")\n'''
if old not in text:
    raise RuntimeError("append_once helper shape changed")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("Prepared Walmart retry applicator")
