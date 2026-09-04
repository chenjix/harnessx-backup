#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Compatibility entry point. New users should use the profile-aware installer;
# VLLM_VENV is retained so existing automation still controls the destination.
HARNESS_VENV="${HARNESS_VENV:-${VLLM_VENV:-$ROOT/.venv-harness}}"
export HARNESS_VENV
bash "$ROOT/scripts/tmax/setup_envs.sh" harness

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  chmod 600 "$ROOT/.env"
  echo "Created $ROOT/.env; fill API keys and cluster-specific paths."
fi

cat <<EOF
Harness environment ready: $HARNESS_VENV

Still required:
  1. Install/configure the Harbor CLI with terminal-bench@2.0.
  2. Ensure Docker is usable.
  3. Create SFT env: bash $ROOT/scripts/tmax/setup_envs.sh sft
  4. Create online RL env: bash $ROOT/scripts/tmax/setup_envs.sh rl
  5. Run: bash $ROOT/scripts/doctor.sh
EOF
