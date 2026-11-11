"""Tests for the pre-commit secret scanner.

Fake secrets are assembled at runtime so this file never contains one.
"""

import pytest

from scripts.check_secrets import blocked_path, scan_text

HEX = "0123456789abcdef"


def fake(*parts: str) -> str:
    return "".join(parts)


FAKE_SECRETS = {
    "anthropic-key": fake("sk-", "ant-", "api03-", "Ab3dEf6hIj9lMn2pQr5tUv8x"),
    "elevenlabs-key": fake("sk", "_", HEX * 3),
    "gemini-api-key": fake("account", "-", "Zx9Kq2Lm7Pw4Rt8Yb3Nd"),
    "quicknode-token": fake("QN", "_", HEX * 2),
    "private-key-pem": fake("-----BEGIN ", "PRIVATE KEY", "-----"),
    "solana-keypair-json": "[" + ",".join(str((i * 37) % 256) for i in range(64)) + "]",
    "quicknode-rpc-url": fake(
        "https://cool-name-1234.solana-devnet.", "quiknode.pro/", HEX * 2
    ),
    "assigned-secret": fake("GEMINI_API_", "SECRET", "=", "3xQ9vT2mK8pL5wR7nB4cJ6hD1fG0"),
}


@pytest.mark.parametrize("rule,secret", FAKE_SECRETS.items())
def test_detects_each_secret_kind(rule, secret):
    findings = scan_text("some/file.py", f"line one\nvalue = {secret}\n")
    assert rule in {f.rule for f in findings}
    assert all(f.line == 2 for f in findings if f.rule == rule)


def test_finding_never_echoes_the_full_secret():
    secret = FAKE_SECRETS["anthropic-key"]
    (finding,) = [f for f in scan_text("a.py", secret) if f.rule == "anthropic-key"]
    assert secret not in finding.preview


@pytest.mark.parametrize(
    "line",
    [
        "ANTHROPIC_API_KEY=",
        "GEMINI_API_KEY=your-key-here",
        'api_key = os.environ["GEMINI_API_KEY"]',
        "API_KEY: process.env.ELEVENLABS_API_KEY,",
        "token = settings.quicknode_stream_security_token",
        "USDC_MINT=4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
        "sig 5nbSbCNEZ6tEtRaT1JTdHTFxNC8YBMNNSmYpeN3NPBwD1SKwGM5J7WvA7P59UwAg5NMKJiSKqcDC6AP5zLGnn2o3",
        "mandate_hash = [1, 2, 3, 4, 5, 6, 7, 8]",
        "X402_FACILITATOR_URL=https://x402.org/facilitator",
    ],
)
def test_ordinary_lines_are_not_flagged(line):
    assert scan_text(".env.example", line) == []


def test_allow_pragma_suppresses_a_line():
    secret = FAKE_SECRETS["quicknode-token"]
    assert scan_text("a.py", f"x = '{secret}'  # secretscan:allow") == []


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "backend/.env",
        ".keys/owner.json",
        "scripts/guardian-keypair.json",
        "programs/vault/target/deploy/vault-keypair.json",
        "id.json",
        "certs/server.pem",
        "certs/server.key",
    ],
)
def test_secret_bearing_paths_are_blocked(path):
    assert blocked_path(path) is not None


@pytest.mark.parametrize(
    "path", [".env.example", "backend/app/config.py", "redteam/attacks.json", "README.md"]
)
def test_normal_paths_are_allowed(path):
    assert blocked_path(path) is None
