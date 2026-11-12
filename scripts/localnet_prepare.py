"""Write the account file that gives a local validator a USDC stand-in.

    python -m scripts.localnet_prepare

LOCALNET ONLY. The file places an SPL mint at the same address as devnet USDC,
with 6 decimals and the *owner wallet* as mint authority, so test USDC can be
minted locally. On devnet the real Circle mint is used and nothing here applies.
"""

from __future__ import annotations

import base64
import json
import struct

from solders.pubkey import Pubkey

from app.chain.client import load_keypair
from app.config import REPO_ROOT, load_chain_settings

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # secretscan:allow (public SPL Token program id)
MINT_RENT_LAMPORTS = 1_461_600  # rent-exempt minimum for an 82-byte account
OUT = REPO_ROOT / "target" / "localnet" / "usdc-mint.json"


def mint_account_data(authority: Pubkey, decimals: int = 6) -> bytes:
    """SPL Token `Mint` layout: 82 bytes."""
    data = (
        struct.pack("<I", 1) + bytes(authority)  # mint_authority: Some(authority)
        + struct.pack("<Q", 0)                   # supply
        + bytes([decimals, 1])                   # decimals, is_initialized
        + struct.pack("<I", 0) + bytes(32)       # freeze_authority: None
    )
    assert len(data) == 82
    return data


def main() -> None:
    settings = load_chain_settings()
    owner = load_keypair(settings.owner_keypair_path).pubkey()
    account = {
        "pubkey": settings.usdc_mint,
        "account": {
            "lamports": MINT_RENT_LAMPORTS,
            "data": [base64.b64encode(mint_account_data(owner)).decode(), "base64"],
            "owner": TOKEN_PROGRAM,
            "executable": False,
            "rentEpoch": 0,
            "space": 82,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(account, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)} (mint {settings.usdc_mint}, authority {owner})")


if __name__ == "__main__":
    main()
