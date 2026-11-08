"""Guardrails for .env.example.

The example file must stay parseable, free of real secrets, and in sync
with the environment variables the runtime actually reads. If a new env
var is added to the code, this test fails until .env.example documents it
(and vice versa: no documented var may be dead).
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Vars a fresh checkout cannot run without (or that gate a whole provider).
REQUIRED_VARS = {
    "DISCORD_TOKEN",
    "TURSO_DATABASE_URL",
    "TURSO_AUTH_TOKEN",
    "TARGET_REDSKY_API_KEY",
    "WALMART_CONSUMER_ID",
    "WALMART_PRIVATE_KEY_B64",
}

# Status words that look like env-var tokens but are doctor-check results.
NON_ENV_TOKENS = {"FAIL", "PASS", "WARN", "INFO"}

# Call shapes through which the runtime reads the environment.
ENV_READ_RE = re.compile(
    r"(os\.getenv|os\.environ\.get|os\.environ\["
    r"|_env_int|_env_float|_env_str"
    r"|_bounded_int|_bounded_float|_bounded_float_env"
    r"|_positive_float_env|_positive_int_env|_boolean_env"
    r"|_priority_env_int|env_bool|env_enabled|env_int|env_float|env_str)"
)
TOKEN_RE = re.compile(r"""['"]([A-Z][A-Z0-9_]{3,})['"]""")
# Some vars are read indirectly via a *_ENV module constant, e.g.
# LOCAL_SQLITE_MODE_ENV = "SNIPERPLUG_LOCAL_SQLITE".
ENV_CONST_RE = re.compile(r"""\w+_ENV\s*=\s*['"]([A-Z][A-Z0-9_]{3,})['"]""")
KEY_VALUE_RE = re.compile(r"^([A-Z][A-Z0-9_]+)=(.*)$")
PLACEHOLDER_MARKERS = ("your-", "example", "changeme", "placeholder", "replace")


def _parse_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in ENV_EXAMPLE.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = KEY_VALUE_RE.match(line)
        assert match is not None, f"unparseable line in .env.example: {raw_line!r}"
        key, value = match.groups()
        assert key not in values, f"duplicate key in .env.example: {key}"
        values[key] = value
    return values


def _code_env_vars() -> set[str]:
    found: set[str] = set()
    for path in list((REPO_ROOT / "sniperplug").rglob("*.py")) + list(
        (REPO_ROOT / "scripts").rglob("*.py")
    ):
        text = path.read_text(errors="ignore")
        for match in ENV_CONST_RE.finditer(text):
            found.add(match.group(1))
        for match in TOKEN_RE.finditer(text):
            name = match.group(1)
            if name in NON_ENV_TOKENS:
                continue
            window = text[max(0, match.start() - 300) : match.start()]
            if ENV_READ_RE.search(window):
                found.add(name)
    return found


def test_env_example_exists_and_parses() -> None:
    assert ENV_EXAMPLE.is_file(), ".env.example is missing from the repo root"
    values = _parse_env_example()
    assert len(values) >= 90, f".env.example looks truncated: {len(values)} vars"


def test_env_example_has_no_secret_looking_values() -> None:
    values = _parse_env_example()
    secret_like = re.compile(r"^[A-Za-z0-9+/=_-]{24,}$")
    for key, value in values.items():
        if not value:
            continue
        lowered = value.lower()
        if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
            continue
        assert not secret_like.match(value), (
            f"{key} has a secret-looking value in .env.example; "
            "use an empty value or a your-... placeholder"
        )


def test_env_example_covers_required_vars() -> None:
    values = _parse_env_example()
    missing = REQUIRED_VARS - set(values)
    assert not missing, f".env.example is missing required vars: {sorted(missing)}"


def test_env_example_matches_code_env_vars() -> None:
    example_vars = set(_parse_env_example())
    code_vars = _code_env_vars()
    assert code_vars, "env-var extractor found nothing; the heuristic broke"
    missing = code_vars - example_vars
    extra = example_vars - code_vars
    assert not missing, (
        "env vars read by the code but missing from .env.example: "
        f"{sorted(missing)}"
    )
    assert not extra, (
        "vars documented in .env.example but not read by any code: "
        f"{sorted(extra)}"
    )
