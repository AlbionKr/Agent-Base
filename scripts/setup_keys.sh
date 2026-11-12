#!/usr/bin/env bash
# Creates the local devnet keypairs in .keys/ (gitignored) if they do not exist,
# and installs the program keypair where `anchor build` expects it.
#
#   bash scripts/wsl_run.sh bash scripts/setup_keys.sh
#
# Never prints secret keys. Prints public keys only.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
mkdir -p .keys target/deploy

for name in owner guardian vault-program; do
  file=".keys/${name}.json"
  if [ ! -f "$file" ]; then
    solana-keygen new --no-bip39-passphrase --silent --outfile "$file" >/dev/null
  fi
  printf '%-14s %s\n' "$name" "$(solana-keygen pubkey "$file")"
done

cp .keys/vault-program.json target/deploy/vault-keypair.json
