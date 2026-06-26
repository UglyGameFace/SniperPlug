from pathlib import Path


def test_aiosqlite_is_declared_for_local_full_suite():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()
    assert "aiosqlite" in requirements
