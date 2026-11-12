"""Generate the QuickNode Streams filter with the wallets to watch.

    python -m scripts.quicknode_filter            # writes quicknode/filter.js
    python -m scripts.quicknode_filter --base64   # also prints it base64-encoded (for the REST API)

Watched owners: the vault account (it owns the vault's USDC token account) and
every agent hot wallet that has a session key in .keys/agents/.
"""

from __future__ import annotations

import argparse
import base64
import json

from solders.pubkey import Pubkey

from app.chain.client import load_keypair
from app.chain.ops import VaultRef
from app.config import REPO_ROOT, load_backend_settings, load_chain_settings

TEMPLATE = REPO_ROOT / "quicknode" / "filter.template.js"
OUTPUT = REPO_ROOT / "quicknode" / "filter.js"


def render(watched: list[str]) -> str:
    return TEMPLATE.read_text(encoding="utf-8").replace("__WATCHED__", json.dumps(sorted(set(watched))))


def watched_owners() -> list[str]:
    chain = load_chain_settings()
    owner = load_backend_settings().owner_address
    ref = VaultRef(Pubkey.from_string(chain.vault_program_id), Pubkey.from_string(chain.usdc_mint), Pubkey.from_string(owner))
    wallets = [str(ref.vault)]
    for key_file in sorted((REPO_ROOT / ".keys" / "agents").glob("*.json")):
        wallets.append(str(load_keypair(key_file).pubkey()))
    return wallets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base64", action="store_true")
    args = parser.parse_args()
    watched = watched_owners()
    source = render(watched)
    OUTPUT.write_text(source, encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} watching {len(watched)} owners:")
    for wallet in watched:
        print(f"  {wallet}")
    if args.base64:
        print("\nfilter_function (base64):")
        print(base64.b64encode(source.encode()).decode())


if __name__ == "__main__":
    main()
