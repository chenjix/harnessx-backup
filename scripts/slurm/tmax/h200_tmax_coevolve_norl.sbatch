#!/bin/bash
# Tmax harness ↔ SFT co-evolution, NO RL, one chain, 8× H200.
#
# Differences from h200_tmax_coevolve.sbatch:
#   ENABLE_RL=0 hard          — no DPPO/GRPO stage, no docker sandbox, no ninja
#   HARNESS_RATCHET=1         — each iteration evolves from the best harness so
#                               far, not merely the most recent gated one
#   RATCHET_ALLOW_TIE=1       — a flat holdout score is adopted provided the run
#                               is clean (SYSERR_TOL / TIE_MAX_SYSERR)
#   ROTATE_EVOLVE_TASKS=1     — the 50-task evolve set is rebuilt every
#                               iteration: tasks that already produced a correct
#                               SFT trajectory retire, unsolved ones carry over,
#                               the rest is refilled from the Tmax taxonomy
#   MIN_TRAJS=80              — a corpus under the floor triggers extra evolve
#                               rounds and a rebuild before SFT runs, up to
#                               MAX_TOPUP_ROUNDS times
#   MAX_TRAJS=0               — no ceiling on selected trajectories; PER_TASK
#                               already bounds per-task breadth
#
# Runtime on 8x H200 (9b, measured over rep1 + rep9): a core iteration is ~7.4 h
# (evolve 3.6 + B eval 1.7 + SFT 0.2 + E eval 1.8); each top-up or SFT retry adds
# ~1.5 h, each extra SFT+eval cycle ~2.1 h. Three iterations plus the anchor eval
# land between ~24 h (clean) and ~59 h (every top-up and retry fires).
#
# Submit a fresh 3-iteration chain:
#   sbatch --export=ALL,REPLICATE=10,N_ITERS=3 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_norl.sbatch
#
# Smoke (1 iteration, 2 evolve rounds, no SFT retries):
#   sbatch --export=ALL,N_ITERS=1,EVOLVE_ROUNDS=2,MAX_SFT_RETRIES=0,REPLICATE=9 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_norl.sbatch
#
# Warm-start from the proven SFT-500 adapter:
#   sbatch --export=ALL,INIT_LORA_PATH=/fsx/home/jixuan.chen/harnessx-backup/outputs/sft/qwen35_9b_tmax_only500_on102_v2 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_norl.sbatch
#
#SBATCH --job-name=tmax-coev-norl
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=4-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coev_norl_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coev_norl_%j.err

set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

echo "===== job ====="
echo "job     : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node    : $(hostname)"
echo "gpus    : ${CUDA_VISIBLE_DEVICES:-unset}"
echo "start   : $(date -u '+%F %T UTC')"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export SFT_PYTHON="${SFT_PYTHON:-$PYTHON_BIN}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

export REPLICATE="${REPLICATE:-1}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}"
# A corpus below MIN_TRAJS buys more data with EXTRA_EVOLVE_ROUNDS and rebuilds,
# up to MAX_TOPUP_ROUNDS times, before SFT runs. MAX_TRAJS=0 removes the ceiling:
# PER_TASK already bounds per-task breadth, so every traj clearing the quality
# gates is kept instead of being discarded to hit a round number.
export MIN_TRAJS="${MIN_TRAJS:-80}"
export MAX_TOPUP_ROUNDS="${MAX_TOPUP_ROUNDS:-3}"
export MAX_TRAJS="${MAX_TRAJS:-0}"
export PER_TASK="${PER_TASK:-3}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-8}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"

# ── ratchet: keep the floor rising, but let genuine ties through ────────────
# A flat holdout score is accepted only when the run is clean: agent_error may
# not exceed the incumbent's by more than SYSERR_TOL, and TIE_MAX_SYSERR caps it
# in absolute terms so a tie cannot inherit the incumbent's error budget.
# TIE_MAX_SYSERR=0 means "a tie counts provided nothing crashed"; raise it if a
# harness never reaches zero agent_error and ties stop being reachable.
export RATCHET_ALLOW_TIE="${RATCHET_ALLOW_TIE:-1}"
export SYSERR_TOL="${SYSERR_TOL:-0}"
export TIE_MAX_SYSERR="${TIE_MAX_SYSERR:-0}"
export MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"

# ── evolve task rotation ────────────────────────────────────────────────────
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export EVOLVE_N_TASKS="${EVOLVE_N_TASKS:-50}"
export EVOLVE_TASK_SEED="${EVOLVE_TASK_SEED:-42}"
export TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$REPO/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"

# ── RL is off in this chain ────────────────────────────────────────────────
export ENABLE_RL=0
export INSTALL_RL_DEPS=0

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/tmax_coevolve"

echo "===== plan ====="
echo "  REPLICATE          : $REPLICATE"
echo "  N_ITERS            : $N_ITERS"
echo "  EVOLVE_ROUNDS      : $EVOLVE_ROUNDS  (+$EXTRA_EVOLVE_ROUNDS on retry)"
echo "  MAX_SFT_RETRIES    : $MAX_SFT_RETRIES"
echo "  traj floor         : $MIN_TRAJS  (top up <=$MAX_TOPUP_ROUNDS x +$EXTRA_EVOLVE_ROUNDS rounds)"
echo "  traj cap           : $( ((MAX_TRAJS>0)) && echo "$MAX_TRAJS" || echo 'none' )   per_task=$PER_TASK"
echo "  INIT_LORA_PATH     : ${INIT_LORA_PATH:-<base>}"
echo "  SEED_HARNESS       : ${SEED_HARNESS:-configs/baseline_tmax_harness.yaml}"
echo "  META_MODEL         : $META_MODEL"
echo "  HARNESS_RATCHET    : $HARNESS_RATCHET"
echo "  RATCHET_ALLOW_TIE  : $RATCHET_ALLOW_TIE  (SYSERR_TOL=$SYSERR_TOL TIE_MAX_SYSERR=$TIE_MAX_SYSERR)"
echo "  ROTATE_EVOLVE_TASKS: $ROTATE_EVOLVE_TASKS  (n=$EVOLVE_N_TASKS seed=$EVOLVE_TASK_SEED)"
echo "  ENABLE_RL          : $ENABLE_RL (this chain is harness-evolve + SFT only)"

# Preflight the pieces the loop cannot recover from, before burning a 4-day slot.
"$PYTHON_BIN" - <<'PY' || { echo "ERROR: preflight failed" >&2; exit 2; }
import os, sys
from pathlib import Path
repo = Path("/fsx/home/jixuan.chen/harnessx-backup")
bad = []
tax = Path(os.environ["TAXONOMY_PARQUET"])
if not tax.is_file():
    bad.append(f"missing taxonomy parquet: {tax}")
if os.environ.get("ROTATE_EVOLVE_TASKS") == "1":
    try:
        import pandas  # noqa: F401
    except ImportError:
        bad.append("pandas is required to sample the taxonomy (ROTATE_EVOLVE_TASKS=1)")
    if not (repo / "scripts/tmax/build_iter_evolve_tasks.py").is_file():
        bad.append("missing scripts/tmax/build_iter_evolve_tasks.py")
if bad:
    print("\n".join("  - " + b for b in bad), file=sys.stderr)
    sys.exit(1)
print("preflight OK")
PY

# Soft Bedrock preflight — the meta-agent needs it, but a transient failure here
# should not kill a job that can retry.
"$PYTHON_BIN" - <<'PY' || echo "WARN: bedrock preflight failed"
import boto3, os
rt = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION_NAME", "us-west-2"))
print("bedrock-runtime OK", rt.meta.region_name)
PY

exec bash "$REPO/scripts/tmax/run_loop_tmax_coevolve.sh"
