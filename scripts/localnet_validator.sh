#!/usr/bin/env bash
# Start a local Solana validator with the vault program and a USDC stand-in.
# LOCALNET ONLY: for verifying chain code without devnet faucet funds.
#
#   python -m scripts.localnet_prepare          (once, on Windows)
#   wsl -d Ubuntu -- bash /mnt/c/.../scripts/wsl_run.sh bash scripts/localnet_validator.sh
#
# RPC: http://127.0.0.1:8899   (the ledger lives in WSL's home dir and is reset each start)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

PROGRAM_ID="8en8vCL4DR8e3Y739FSgKtLzFvTwiMvD8GVgnKGTBV2x"
USDC_MINT="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
MINT_FILE="target/localnet/usdc-mint.json"

[ -f target/deploy/vault.so ] || { echo "target/deploy/vault.so missing: run anchor build first" >&2; exit 1; }
[ -f "$MINT_FILE" ] || { echo "$MINT_FILE missing: run python -m scripts.localnet_prepare first" >&2; exit 1; }

exec solana-test-validator \
  --reset \
  --ledger "$HOME/agentbase-localnet-ledger" \
  --bpf-program "$PROGRAM_ID" target/deploy/vault.so \
  --account "$USDC_MINT" "$MINT_FILE" \
  --quiet
