#!/usr/bin/env bash
# Create the python env open-instruct RL actually needs, from its own lockfile.
#
#   bash scripts/tmax/setup_rl_env.sh
#   RL_VENV=/fsx/home/jixuan.chen/oi-venv bash scripts/tmax/setup_rl_env.sh   # custom location
#
# Why not reuse the existing envs: grpo_fast needs ray + deepspeed + openenv-core
# + vllm + liger-kernel + flash-attn + ai2-olmo-core together, and
#   /fsx/home/jixuan.chen/.venv                      has ray+vllm, no deepspeed/openenv
#   miniconda3/envs/vllm-019-cu128-clean             has trl, none of the RL deps
# open-instruct pins all of it in uv.lock (python 3.12, torch 2.10+cu128, a
# prebuilt flash-attn wheel), so `uv sync` reproduces the official env exactly
# instead of us guessing versions into an env built for something else.
#
# causal-conv1d ships sdist-only and compiles against CUDA; nvcc 12.8 matches the
# locked torch cu128 build, so CUDA_HOME is pinned to it here.

set -Eeuo pipefail
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
OI="${OPEN_INSTRUCT_ROOT:-$ROOT/tmax/training/open-instruct}"
[[ -d "$OI/open_instruct" ]] || { echo "ERROR: no open-instruct at $OI" >&2; exit 2; }
cd "$OI"

command -v uv >/dev/null || { echo "ERROR: uv not on PATH (expected ~/.local/bin/uv)" >&2; exit 2; }

export MAX_JOBS="${MAX_JOBS:-16}"           # cap parallel nvcc jobs (causal-conv1d)
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"  # Lustre: hardlinks across filesystems fail
if [[ -n "${RL_VENV:-}" ]]; then
  export UV_PROJECT_ENVIRONMENT="$RL_VENV"
fi
VENV="${RL_VENV:-$OI/.venv}"
PY="$VENV/bin/python"

echo "===== open-instruct RL env ====="
echo "  project : $OI"
echo "  uv      : $(uv --version)"
echo "  venv    : $VENV"
echo "  MAX_JOBS: $MAX_JOBS"
echo
echo "Downloads torch/vllm/flash-attn wheels, then compiles causal-conv1d — 15-40 min."

# ── phase 1: everything that ships as a wheel ───────────────────────────────
# causal-conv1d is held back: it is sdist-only and builds a CUDA extension, and
# in an isolated build env uv installs a FRESH torch from PyPI (currently cu130)
# as its build dependency. That build torch then disagrees with both the locked
# runtime torch (2.10+cu128) and every CUDA toolkit on this node, and the build
# dies with "The detected CUDA version (12.8) mismatches the version that was
# used to compile PyTorch (13.0)". upstream pyproject only wires
# `match-runtime = true` for flash-attn/flash-attn-3, not for this package.
echo "--- phase 1/2: sync without causal-conv1d"
uv sync --frozen --no-install-package causal-conv1d 2>&1 | tail -25
[[ -x "$PY" ]] || { echo "ERROR: uv sync produced no interpreter at $PY" >&2; exit 2; }

# ── phase 2: build causal-conv1d against the torch we just installed ────────
TORCH_CUDA="$("$PY" -c 'import torch;print(torch.version.cuda or "")' 2>/dev/null || true)"
TORCH_VER="$("$PY" -c 'import torch;print(torch.__version__)' 2>/dev/null || true)"
echo "--- phase 2/2: causal-conv1d against torch $TORCH_VER (cuda $TORCH_CUDA)"
CC_OK=0
if [[ -z "$TORCH_CUDA" ]]; then
  echo "WARN: could not read torch.version.cuda — skipping causal-conv1d"
else
  CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-$TORCH_CUDA}"
  if [[ ! -x "$CUDA_HOME/bin/nvcc" ]]; then
    echo "WARN: no nvcc at $CUDA_HOME/bin/nvcc (need a CUDA $TORCH_CUDA toolkit to match torch)."
    echo "      available: $(ls -d /usr/local/cuda-* 2>/dev/null | tr '\n' ' ')"
  else
    export CUDA_HOME
    export PATH="$CUDA_HOME/bin:$PATH"
    echo "      CUDA_HOME=$CUDA_HOME ($("$CUDA_HOME/bin/nvcc" --version | tail -1))"
    # --no-build-isolation-package: build in the project env, so it compiles
    # against the cu128 torch already installed instead of pulling its own.
    # --inexact: keep the build tools we just added from being pruned mid-sync.
    uv pip install --quiet --python "$PY" setuptools wheel ninja packaging || true
    if uv sync --frozen --inexact --no-build-isolation-package causal-conv1d 2>&1 | tail -25; then
      CC_OK=1
    fi
  fi
fi
if (( ! CC_OK )); then
  echo
  echo "WARN: causal-conv1d not installed. RL still runs: open_instruct's Qwen3.5"
  echo "      packing patch guards on it (\`if self.causal_conv1d_fn is not None\`)"
  echo "      and falls back to F.silu(self.conv1d(...)) — correct, just slower on"
  echo "      the linear-attention layers. Retry later with:"
  echo "        CUDA_HOME=/usr/local/cuda-\$(<torch cuda>) uv sync --frozen --inexact \\"
  echo "          --no-build-isolation-package causal-conv1d"
fi

echo
echo "===== verifying ====="
"$PY" - <<'PY'
import importlib.util as u
from importlib.metadata import version
need = ["ray", "deepspeed", "openenv", "vllm", "torch", "transformers", "liger_kernel",
        "flash_attn", "datasets", "docker", "fla"]
optional = ["causal_conv1d"]          # guarded fallback exists in the packing patch
missing = [m for m in need if u.find_spec(m) is None]
for m in need + optional:
    v = ""
    if u.find_spec(m):
        try:
            v = version({"openenv": "openenv-core", "liger_kernel": "liger-kernel",
                         "fla": "flash-linear-attention"}.get(m, m))
        except Exception:
            v = "?"
    tag = "yes" if u.find_spec(m) else ("NO(opt)" if m in optional else "NO ")
    print(f"  {m:16s} {tag:8s} {v}")
if missing:
    raise SystemExit(f"\nERROR: still missing: {missing}")
print("\nall RL deps present")
PY

echo
echo "Use it with:"
echo "  export RL_PYTHON=$PY"
