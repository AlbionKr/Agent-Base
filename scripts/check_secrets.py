"""Pre-commit secret scan. Blocks commits that contain credentials or key files.

Usage:
    python scripts/check_secrets.py --staged      # what the git hook runs
    python scripts/check_secrets.py --all         # every tracked file
    python scripts/check_secrets.py PATH [PATH…]  # specific files

A line can opt out with the marker `secretscan:allow` (use it only for public
values that merely look like secrets). Stdlib only, so the hook works before
any dependency is installed.
"""

from __future__ import annotations

import argparse
import fnmatch
import math
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ALLOW_MARKER = "secretscan:allow"
MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    preview: str


RULES: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("elevenlabs-key", re.compile(r"\bsk_[a-f0-9]{40,}\b")),
    ("gemini-api-key", re.compile(r"\b(?:account|master)-[A-Za-z0-9]{16,}\b")),
    ("quicknode-token", re.compile(r"\bQN_[a-f0-9]{32}\b")),
    (
        "quicknode-rpc-url",
        re.compile(r"https://[a-z0-9\-]+\.solana-[a-z]+\.quiknode\.pro/[a-f0-9]{20,}"),
    ),
    ("x-bearer-token", re.compile(r"\bA{20,}[A-Za-z0-9%]{30,}")),
    ("private-key-pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # A Solana keypair file is a JSON array of 64 byte values.
    ("solana-keypair-json", re.compile(r"\[\s*(?:\d{1,3}\s*,\s*){63}\d{1,3}\s*\]")),
]

# NAME_WITH_SECRET_WORD = <long opaque value>
ASSIGNED = re.compile(
    r"(?i)\b[A-Z0-9_]*(?:SECRET|PRIVATE_KEY|API_KEY|TOKEN|PASSWORD)[A-Z0-9_]*"
    r"\s*[=:]\s*['\"]?([A-Za-z0-9+/_\-=]{20,})"
)


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _looks_opaque(value: str) -> bool:
    has_digit = any(c.isdigit() for c in value)
    has_alpha = any(c.isalpha() for c in value)
    return has_digit and has_alpha and _entropy(value) >= 3.0


def _redact(secret: str) -> str:
    return f"{secret[:6]}...[{len(secret)} chars]"


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for rule, pattern in RULES:
            match = pattern.search(line)
            if match:
                findings.append(Finding(path, number, rule, _redact(match.group(0))))
        match = ASSIGNED.search(line)
        if match and _looks_opaque(match.group(1)):
            findings.append(Finding(path, number, "assigned-secret", _redact(match.group(1))))
    return findings


def blocked_path(path: str) -> str | None:
    """Reason this path must never be committed, or None if it is fine."""
    posix = PurePosixPath(path.replace("\\", "/"))
    name = posix.name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "env file (only .env.example may be committed)"
    if ".keys" in posix.parts:
        return "file inside .keys/ (keypair directory)"
    if fnmatch.fnmatch(name, "*keypair*.json") or name == "id.json":
        return "Solana keypair file"
    if posix.suffix in {".pem", ".key"}:
        return "private key / certificate file"
    return None


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def _staged_files() -> list[str]:
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _tracked_files() -> list[str]:
    return [p for p in _git("ls-files", "-z").decode("utf-8", "replace").split("\0") if p]


def _decode(blob: bytes) -> str | None:
    if b"\0" in blob[:8192]:
        return None  # binary
    return blob[:MAX_BYTES].decode("utf-8", "replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    if args.paths:
        sources = [(p, lambda p=p: Path(p).read_bytes()) for p in args.paths]
    elif args.all:
        sources = [(p, lambda p=p: Path(p).read_bytes()) for p in _tracked_files()]
    else:
        # Scan the staged blob, not the working copy: that is what gets committed.
        sources = [(p, lambda p=p: _git("show", f":{p}")) for p in _staged_files()]

    problems: list[str] = []
    for path, read in sources:
        reason = blocked_path(path)
        if reason:
            problems.append(f"  {path}: blocked path - {reason}")
            continue
        text = _decode(read())
        if text is None:
            continue
        for f in scan_text(path, text):
            problems.append(f"  {f.path}:{f.line}: {f.rule} - {f.preview}")

    if problems:
        print("secret scan FAILED - commit blocked:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print(
            f"\nMove secrets to .env (gitignored). If a value is public and only looks "
            f"like a secret, add '{ALLOW_MARKER}' on that line.",
            file=sys.stderr,
        )
        return 1
    print(f"secret scan ok ({len(sources)} file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
