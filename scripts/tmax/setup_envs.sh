#!/usr/bin/env bash
# Build the separate environments used by the Tmax pipeline.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROFILE="${1:-help}"

usage() {
  cat <<'EOF'
Usage: bash scripts/tmax/setup_envs.sh <harness|sft|rl|all>

Optional locations:
  HARNESS_VENV=/path/to/venv   default: <repo>/.venv-harness
  SFT_VENV=/path/to/venv       default: <repo>/.venv-sft
  RL_VENV=/path/to/venv        default: tmax/training/open-instruct/.venv

The RL environment is reproduced from open-instruct/uv.lock. Harness and SFT
use the root requirements files. GPU environments require a compatible driver,
CUDA runtime, and (for some builds) CUDA toolkit.
EOF
}

need_python() {
  command -v python3 >/dev/null || { echo 'ERROR: python3 is required' >&2; exit 2; }
}

setup_harness() {
  need_python
  local venv="${HARNESS_VENV:-$ROOT/.venv-harness}"
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install --upgrade pip setuptools wheel
  "$venv/bin/python" -m pip install -e "$ROOT" -r "$ROOT/requirements-evolve.txt"
  echo "Harness environment: $venv"
  echo "Install a cluster-compatible vLLM build here before local model serving."
  "$venv/bin/python" "$ROOT/scripts/tmax/check_env.py" harness
}

setup_sft() {
  need_python
  local venv="${SFT_VENV:-$ROOT/.venv-sft}"
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install --upgrade pip setuptools wheel
  "$venv/bin/python" -m pip install -r "$ROOT/requirements-sft.txt"
  echo "SFT environment: $venv"
  "$venv/bin/python" "$ROOT/scripts/tmax/check_env.py" sft
}

setup_rl() {
  command -v uv >/dev/null || {
    echo 'ERROR: uv is required for the locked RL environment.' >&2
    echo 'Install uv, then rerun this command.' >&2
    exit 2
  }
  bash "$ROOT/scripts/tmax/setup_rl_env.sh"
}

case "$PROFILE" in
  harness) setup_harness ;;
  sft) setup_sft ;;
  rl) setup_rl ;;
  all) setup_harness; setup_sft; setup_rl ;;
  help|-h|--help) usage ;;
  *) echo "Unknown profile: $PROFILE" >&2; usage >&2; exit 2 ;;
esac
