#!/usr/bin/env bash
# Run a toolchain command (anchor, cargo, solana, ...) from the repo root with
# the WSL toolchain on PATH. From Windows:
#
#   wsl -d Ubuntu -- bash /mnt/c/path/to/repo/scripts/wsl_run.sh anchor build
#
# Load the toolchain PATH before enabling strict mode: profile scripts are not
# written for `set -u`.
# shellcheck disable=SC1091
. "$HOME/.profile" >/dev/null 2>&1 || true
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
exec "$@"
