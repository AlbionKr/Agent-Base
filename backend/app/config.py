"""Runtime configuration, read from the environment (.env at the repo root)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

DEVNET_CAIP2 = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
DEVNET_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"


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
