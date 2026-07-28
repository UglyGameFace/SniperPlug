from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF_PATH = Path(__file__).resolve().relative_to(ROOT).as_posix()

SKIP_PARTS = {
    ".git",
    ".astro",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "__pycache__",
}
SKIP_FILES = {
    SELF_PATH,
    "website/package-lock.json",
}
MAX_FILE_BYTES = 2_000_000

HIGH_CONFIDENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255})\b"),
    ),
    (
        "Discord bot token",
        re.compile(r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,})\b"),
    ),
    (
        "Discord webhook",
        re.compile(r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9._-]+"),
    ),
    (
        "AWS access key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    (
        "Google API key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    (
        "Stripe live secret",
        re.compile(r"\bsk_live_[0-9A-Za-z]{16,}\b"),
    ),
    (
        "Slack token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ),
    (
        "signed JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
)

SENSITIVE_NAMES = (
    "DISCORD_TOKEN",
    "BOT_TOKEN",
    "BESTBUY_API_KEY",
    "WALMART_PRIVATE_KEY_B64",
    "WALMART_CONSUMER_ID",
    "WALMART_PUBLISHER_ID",
    "DEAL_DESK_PASSWORD",
    "DEAL_DESK_SESSION_SECRET",
    "GITHUB_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "WHOP_TOKEN_SECRET",
    "WHOP_CLIENT_SECRET",
    "SUPABASE_SERVICE_ROLE_KEY",
    "DATABASE_URL",
)
SENSITIVE_ASSIGNMENT = re.compile(
    rf"\b(?P<name>{'|'.join(map(re.escape, SENSITIVE_NAMES))})\b\s*[:=]\s*"
    r"(?P<value>[^\s#;,]+|\"[^\"\n]*\"|'[^'\n]*')"
)

PLACEHOLDER_MARKERS = (
    "replace",
    "example",
    "placeholder",
    "dummy",
    "changeme",
    "change-me",
    "your_",
    "your-",
    "test-token",
    "high-entropy secret",
    "app_replace_me",
    "github_pat_replace_me",
    "os.getenv",
    "process.env",
    "import.meta.env",
    "${",
    "secrets.",
    "env.",
)


def normalized_value(raw: str) -> str:
    return raw.strip().strip('"\'').strip()


def looks_like_placeholder(raw: str) -> bool:
    value = normalized_value(raw)
    lowered = value.lower()
    if not value or value in {"''", '""', "none", "null"}:
        return True
    if value.startswith("<"):
        return True
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def scan_line(text: str) -> set[str]:
    findings: set[str] = set()
    for label, pattern in HIGH_CONFIDENCE_PATTERNS:
        if pattern.search(text):
            findings.add(label)

    for match in SENSITIVE_ASSIGNMENT.finditer(text):
        if not looks_like_placeholder(match.group("value")):
            findings.add(f"literal value assigned to {match.group('name')}")
    return findings


def is_skipped(path: Path | str) -> bool:
    candidate = Path(path)
    posix = candidate.as_posix().lstrip("./")
    if posix in SKIP_FILES:
        return True
    return any(part in SKIP_PARTS for part in candidate.parts)


def scan_worktree() -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if is_skipped(relative):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            data = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(data.splitlines(), start=1):
            for label in scan_line(line):
                findings.append((relative.as_posix(), line_number, label))
    return findings


def git_history_patch() -> str:
    result = subprocess.run(
        [
            "git",
            "log",
            "-p",
            "--all",
            "--full-history",
            "--no-ext-diff",
            "--no-renames",
            "--format=commit:%H",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def scan_history() -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    commit = "unknown"
    path = "unknown"
    for line in git_history_patch().splitlines():
        if line.startswith("commit:"):
            commit = line.removeprefix("commit:").strip()
            continue
        if line.startswith("diff --git a/"):
            parts = line.split(" b/", maxsplit=1)
            path = parts[1] if len(parts) == 2 else "unknown"
            continue
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        if is_skipped(path):
            continue
        for label in scan_line(line[1:]):
            findings.append((commit, path, label))
    return findings


def refs_containing(commit: str) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "branch", "-r", "--contains", commit, "--format=%(refname:short)"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    refs = sorted({line.strip() for line in result.stdout.splitlines() if line.strip() and "->" not in line})
    return tuple(refs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail when public repository content appears to contain a credential.")
    parser.add_argument("--history", action="store_true", help="Also scan every Git commit patch reachable from all refs.")
    args = parser.parse_args()

    worktree_findings = scan_worktree()
    history_findings = scan_history() if args.history else []

    if worktree_findings or history_findings:
        print("PUBLIC SECRET SCAN FAILED", file=sys.stderr)
        for path, line_number, label in worktree_findings:
            print(f"- working tree: {path}:{line_number} — {label}", file=sys.stderr)
        seen: set[tuple[str, str, str]] = set()
        ref_cache: dict[str, tuple[str, ...]] = {}
        for commit, path, label in history_findings:
            key = (commit, path, label)
            if key in seen:
                continue
            seen.add(key)
            refs = ref_cache.setdefault(commit, refs_containing(commit))
            rendered_refs = ", ".join(refs[:12]) if refs else "no current remote branch"
            if len(refs) > 12:
                rendered_refs += f", plus {len(refs) - 12} more"
            print(
                f"- history: commit {commit[:12]} in {path} — {label}; refs: {rendered_refs}",
                file=sys.stderr,
            )
        print("Matched values are intentionally redacted. Rotate any real credential before removing it from Git history.", file=sys.stderr)
        return 1

    scope = "working tree and Git history" if args.history else "working tree"
    print(f"PUBLIC SECRET SCAN PASSED: {scope} contain no high-confidence committed credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
