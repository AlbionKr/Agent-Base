#!/usr/bin/env bash
# Installs the Solana toolchain needed to build programs/vault, inside WSL or Linux.
#
#   bash scripts/setup_wsl_toolchain.sh
#
# Same steps as the official installer (https://solana-install.solana.workers.dev),
# minus Surfpool and the global AI-agent skill, which this project does not use:
#   1. apt build dependencies
#   2. Rust via rustup            (https://sh.rustup.rs)
#   3. Solana CLI, stable channel (https://release.anza.xyz)
#   4. Anchor CLI via avm         (https://github.com/solana-foundation/anchor)
#   5. Node.js via nvm, and Yarn  (used by `anchor test`)
# Safe to re-run: each step is skipped if the tool is already present.
set -euo pipefail

ANCHOR_VERSION="${ANCHOR_VERSION:-1.1.2}"
NODE_VERSION="${NODE_VERSION:-24.10.0}"
NVM_VERSION="v0.40.3"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi
export DEBIAN_FRONTEND=noninteractive

echo "[1/5] apt build dependencies"
$SUDO apt-get update -y
$SUDO apt-get install -y curl git build-essential pkg-config libudev-dev llvm \
  libclang-dev protobuf-compiler libssl-dev

echo "[2/5] Rust"
if ! command -v rustc >/dev/null 2>&1 && [ ! -x "$HOME/.cargo/bin/rustc" ]; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
# shellcheck disable=SC1091
. "$HOME/.cargo/env"

echo "[3/5] Solana CLI"
export PATH="$HOME/.local/share/solana/install/active_release/bin:$PATH"
if ! command -v solana >/dev/null 2>&1; then
  sh -c "$(curl --proto '=https' --tlsv1.2 -sSfL https://release.anza.xyz/stable/install)"
fi

echo "[4/5] Anchor CLI ${ANCHOR_VERSION}"
export PATH="$HOME/.avm/bin:$PATH"
if ! command -v anchor >/dev/null 2>&1; then
  cargo install --git https://github.com/solana-foundation/anchor \
    --tag "v${ANCHOR_VERSION}" avm --locked
  avm install "${ANCHOR_VERSION}"
  avm use "${ANCHOR_VERSION}"
fi

echo "[5/5] Node.js ${NODE_VERSION} and Yarn"
export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  curl --proto '=https' --tlsv1.2 -sSfo- \
    "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" | bash
fi
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"
nvm install "${NODE_VERSION}" >/dev/null
nvm alias default "${NODE_VERSION}" >/dev/null
command -v yarn >/dev/null 2>&1 || npm install --global yarn

# Make the tools available to non-interactive shells (`wsl -- bash -lc ...`).
PROFILE="$HOME/.profile"
MARK="# agent-base toolchain"
if ! grep -qF "$MARK" "$PROFILE" 2>/dev/null; then
  {
    echo ""
    echo "$MARK"
    echo 'export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.avm/bin:$PATH"'
    echo 'export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'
  } >> "$PROFILE"
fi

echo ""
echo "Installed versions:"
echo "  rust:   $(rustc --version)"
echo "  solana: $(solana --version)"
echo "  anchor: $(anchor --version)"
echo "  node:   $(node --version)"
echo "  yarn:   $(yarn --version)"
