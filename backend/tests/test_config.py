"""Backend settings come from the environment and fail loudly when wrong."""

from __future__ import annotations

import pytest

from app.config import DEVNET_USDC_MINT, REPO_ROOT, backend_settings_from
from policy_fixtures import addr

OWNER = addr(1)


def test_defaults_need_only_the_owner_address():
    s = backend_settings_from({"OWNER_ADDRESS": OWNER})
    assert s.owner_address == OWNER
    assert (s.api_port, s.webhook_port) == (8000, 8001)
    assert s.audit_db_path == REPO_ROOT / "data" / "audit.sqlite"
    assert s.frontend_origins == frozenset({"http://localhost:5173", "http://127.0.0.1:5173"})
    assert s.usdc_mint == DEVNET_USDC_MINT
    assert s.quicknode_security_token == ""
    assert s.approval_timeout_seconds == 60


def test_values_are_read_and_trimmed():
    s = backend_settings_from(
        {
            "OWNER_ADDRESS": f"  {OWNER} ",
            "BACKEND_PORT": "9000",
            "WEBHOOK_PORT": "9001",
            "AUDIT_DB_PATH": "tmp/x.sqlite",
            "FRONTEND_ORIGINS": "http://localhost:3000 , http://127.0.0.1:3000/",
            "QUICKNODE_STREAM_SECURITY_TOKEN": " tok ",
            "APPROVAL_TIMEOUT_SECONDS": "5",
        }
    )
    assert (s.api_port, s.webhook_port) == (9000, 9001)
    assert s.audit_db_path == REPO_ROOT / "tmp" / "x.sqlite"
    assert s.frontend_origins == frozenset({"http://localhost:3000", "http://127.0.0.1:3000"})
    assert s.quicknode_security_token == "tok"
    assert s.approval_timeout_seconds == 5


@pytest.mark.parametrize(
    "env, word",
    [
        ({}, "OWNER_ADDRESS"),
        ({"OWNER_ADDRESS": "not-an-address"}, "OWNER_ADDRESS"),
        ({"OWNER_ADDRESS": OWNER, "BACKEND_PORT": "eighty"}, "BACKEND_PORT"),
        ({"OWNER_ADDRESS": OWNER, "BACKEND_PORT": "8000", "WEBHOOK_PORT": "8000"}, "different"),
        ({"OWNER_ADDRESS": OWNER, "APPROVAL_TIMEOUT_SECONDS": "0"}, "APPROVAL_TIMEOUT_SECONDS"),
        ({"OWNER_ADDRESS": OWNER, "FRONTEND_ORIGINS": "*"}, "FRONTEND_ORIGINS"),
    ],
)
def test_bad_settings_stop_the_server_with_a_clear_message(env, word):
    with pytest.raises(RuntimeError) as info:
        backend_settings_from(env)
    assert word in str(info.value)
