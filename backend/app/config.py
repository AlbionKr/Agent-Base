"""Runtime configuration, read from the environment (.env at the repo root)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv
from solders.pubkey import Pubkey

REPO_ROOT = Path(__file__).resolve().parents[2]

DEVNET_CAIP2 = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
DEVNET_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
DEFAULT_FRONTEND_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


@dataclass(frozen=True)
class ChainSettings:
    rpc_url: str
    network_caip2: str
    usdc_mint: str
    vault_program_id: str
    owner_keypair_path: Path
    guardian_keypair_path: Path


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def load_chain_settings() -> ChainSettings:
    load_dotenv(REPO_ROOT / ".env")
    program_id = os.environ.get("VAULT_PROGRAM_ID", "").strip()
    if not program_id:
        raise RuntimeError("VAULT_PROGRAM_ID is not set (see .env.example)")
    return ChainSettings(
        rpc_url=os.environ.get("SOLANA_RPC_URL", "https://api.devnet.solana.com"),
        network_caip2=os.environ.get("SOLANA_NETWORK_CAIP2", DEVNET_CAIP2),
        usdc_mint=os.environ.get("USDC_MINT", DEVNET_USDC_MINT),
        vault_program_id=program_id,
        owner_keypair_path=_path(os.environ.get("OWNER_KEYPAIR_PATH", ".keys/owner.json")),
        guardian_keypair_path=_path(
            os.environ.get("GUARDIAN_KEYPAIR_PATH", ".keys/guardian.json")
        ),
    )


# ---------------------------------------------------------------- backend


@dataclass(frozen=True)
class BackendSettings:
    owner_address: str
    api_port: int
    webhook_port: int
    audit_db_path: Path
    frontend_origins: frozenset[str]
    usdc_mint: str
    quicknode_security_token: str
    approval_timeout_seconds: int


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int = 1) -> int:
    raw = env.get(name, "").strip() or str(default)
    if not raw.isdigit() or int(raw) < minimum:
        raise RuntimeError(f"{name} must be a whole number >= {minimum}, got {raw!r}")
    return int(raw)


def backend_settings_from(env: Mapping[str, str]) -> BackendSettings:
    """Build settings from an environment mapping. Raises RuntimeError with the variable name."""
    owner = env.get("OWNER_ADDRESS", "").strip()
    try:
        Pubkey.from_string(owner)
    except Exception:
        raise RuntimeError(
            "OWNER_ADDRESS must be the vault owner's Solana address (see .env.example); "
            "mandates are only accepted when signed by it"
        ) from None

    api_port = _int(env, "BACKEND_PORT", 8000)
    webhook_port = _int(env, "WEBHOOK_PORT", 8001)
    if api_port == webhook_port:
        raise RuntimeError("BACKEND_PORT and WEBHOOK_PORT must be different ports")

    origins = frozenset(
        origin.strip().rstrip("/")
        for origin in env.get("FRONTEND_ORIGINS", DEFAULT_FRONTEND_ORIGINS).split(",")
        if origin.strip()
    )
    if not origins or any(not o.startswith(("http://", "https://")) for o in origins):
        raise RuntimeError("FRONTEND_ORIGINS must be a comma-separated list of http(s) origins")

    return BackendSettings(
        owner_address=owner,
        api_port=api_port,
        webhook_port=webhook_port,
        audit_db_path=_path(env.get("AUDIT_DB_PATH", "").strip() or "data/audit.sqlite"),
        frontend_origins=origins,
        usdc_mint=env.get("USDC_MINT", "").strip() or DEVNET_USDC_MINT,
        quicknode_security_token=env.get("QUICKNODE_STREAM_SECURITY_TOKEN", "").strip(),
        approval_timeout_seconds=_int(env, "APPROVAL_TIMEOUT_SECONDS", 60),
    )


def load_backend_settings() -> BackendSettings:
    load_dotenv(REPO_ROOT / ".env")
    return backend_settings_from(os.environ)
