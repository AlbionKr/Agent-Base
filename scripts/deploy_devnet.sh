#!/usr/bin/env bash
# Build the vault program, deploy it to Solana devnet, and publish the IDL to
# the backend. Run inside WSL/Linux:
#
#   bash scripts/wsl_run.sh bash scripts/deploy_devnet.sh
#
# Needs devnet SOL in .keys/owner.json (https://faucet.solana.com). A first
# deploy costs roughly 2-3 SOL of rent; upgrades are close to free.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

WALLET=".keys/owner.json"
CLUSTER="devnet"

[ -f "$WALLET" ] || { echo "missing $WALLET - run scripts/setup_keys.sh first" >&2; exit 1; }
cp .keys/vault-program.json target/deploy/vault-keypair.json 2>/dev/null || {
  mkdir -p target/deploy && cp .keys/vault-program.json target/deploy/vault-keypair.json
}

echo "== build"
anchor build

echo "== wallet"
echo "owner   $(solana-keygen pubkey "$WALLET")"
echo "balance $(solana balance "$(solana-keygen pubkey "$WALLET")" --url "$CLUSTER")"

echo "== deploy to $CLUSTER"
anchor deploy --provider.cluster "$CLUSTER" --provider.wallet "$WALLET"

echo "== publish IDL to backend"
mkdir -p backend/app/chain/idl
cp target/idl/vault.json backend/app/chain/idl/vault.json

PROGRAM_ID="$(solana-keygen pubkey target/deploy/vault-keypair.json)"
echo ""
echo "program id: $PROGRAM_ID"
echo "explorer:   https://explorer.solana.com/address/$PROGRAM_ID?cluster=$CLUSTER"
