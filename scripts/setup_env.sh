#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VLLM_VENV:-$ROOT/.venv}"

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -e "$ROOT"
"$VENV/bin/python" -m pip install -r "$ROOT/requirements-evolve.txt"

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  chmod 600 "$ROOT/.env"
  echo "Created $ROOT/.env; fill API keys and cluster-specific paths."
fi

cat <<EOF
Base environment ready: $VENV

Still required:
  1. Install/configure the Harbor CLI with terminal-bench@2.0.
  2. Ensure Docker is usable.
  3. Create the SFT conda env and install requirements-sft.txt.
  4. For replay-GRPO, configure Slime/Megatron and requirements-grpo.txt.
  5. Run: bash $ROOT/scripts/doctor.sh
EOF
