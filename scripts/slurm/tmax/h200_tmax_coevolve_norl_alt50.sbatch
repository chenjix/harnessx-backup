#!/bin/bash
# Tmax harness ↔ SFT co-evolution, NO RL, 9b, alternate initial evolve-50.
#
# Same protocol as h200_tmax_coevolve_norl.sbatch (rep10):
#   ENABLE_RL=0, HARNESS_RATCHET=1, RATCHET_ALLOW_TIE=1, ROTATE_EVOLVE_TASKS=1
#   MIN_TRAJS=80, MAX_TRAJS=0, 5 iterations, 5 evolve rounds per iter
#
# The one intended difference vs rep1 / rep10:
#   Iter-1 evolve seed is a new stratified 50, disjoint from holdout-102 AND
#   from every evolve set those two chains used (seed-50 + rep10 i2/i3 refill).
#   Later iterations still rotate 50 (retire SFT-solved, keep unsolved, refill)
#   and keep applying the same extra-exclude so they cannot drift back onto
#   the previous experiments' tasks.
#
# Holdout stays the Tmax 102 (eval only). Evolve never trains on those 102.
#
# Runtime: 5 iters scale from the measured ~7.4 h/iter (plus ~1.6 h anchor).
# Clean chain ~40 h; every top-up and SFT retry can push toward ~4-5 days.
# --requeue plus .done markers mean a timeout is a resume, not a restart.
#
# Submit:
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_norl_alt50.sbatch
#
# Resume after preemption (same command; .done markers skip finished stages):
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_norl_alt50.sbatch
#
# Smoke (1 iter, 2 evolve rounds, no SFT retries):
#   sbatch --export=ALL,N_ITERS=1,EVOLVE_ROUNDS=2,MAX_SFT_RETRIES=0,MAX_TOPUP_ROUNDS=0 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_norl_alt50.sbatch
#
#SBATCH --job-name=tmax-coev-a50
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=5-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coev_alt50_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coev_alt50_%j.err

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

# Fresh chain — do not reuse REPLICATE=1/9/10 (those own on-disk state).
export REPLICATE="${REPLICATE:-12}"
export N_ITERS="${N_ITERS:-5}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}"
export MIN_TRAJS="${MIN_TRAJS:-80}"
export MAX_TOPUP_ROUNDS="${MAX_TOPUP_ROUNDS:-3}"
export MAX_TRAJS="${MAX_TRAJS:-0}"
export PER_TASK="${PER_TASK:-3}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-2}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"

export RATCHET_ALLOW_TIE="${RATCHET_ALLOW_TIE:-1}"
export SYSERR_TOL="${SYSERR_TOL:-0}"
export TIE_MAX_SYSERR="${TIE_MAX_SYSERR:-0}"
export MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"

export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export EVOLVE_N_TASKS="${EVOLVE_N_TASKS:-50}"
# Distinct from the old seed=42 used by rep1/rep10 (i1 was 43, i2 44, i3 45).
export EVOLVE_TASK_SEED="${EVOLVE_TASK_SEED:-77}"
export TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$REPO/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"

# New iter-1 50, plus a denylist so later refills cannot pick prior-experiment ids.
export EVOLVE_TASKS_JSON="${EVOLVE_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_alt12_list.json}"
export EVOLVE_EXTRA_EXCLUDE="${EVOLVE_EXTRA_EXCLUDE:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve_used_rep1_rep10.json}"

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
echo "  EVOLVE_TASKS_JSON  : $EVOLVE_TASKS_JSON"
echo "  EVOLVE_EXTRA_EXCLUDE: $EVOLVE_EXTRA_EXCLUDE"
echo "  ENABLE_RL          : $ENABLE_RL (this chain is harness-evolve + SFT only)"

"$PYTHON_BIN" - <<'PY' || { echo "ERROR: preflight failed" >&2; exit 2; }
import json, os, sys
from pathlib import Path
repo = Path("/fsx/home/jixuan.chen/harnessx-backup")
bad = []
tax = Path(os.environ["TAXONOMY_PARQUET"])
if not tax.is_file():
    bad.append(f"missing taxonomy parquet: {tax}")
seed = Path(os.environ["EVOLVE_TASKS_JSON"])
exc = Path(os.environ["EVOLVE_EXTRA_EXCLUDE"])
hold = repo / "recipe/tb2_evolver/tasks_tmax_only200.json"
old = repo / "recipe/tb2_evolver/tasks_tmax_evolve50_list.json"
if not seed.is_file():
    bad.append(f"missing alt evolve seed: {seed}")
if not exc.is_file():
    bad.append(f"missing extra-exclude list: {exc}")
if not hold.is_file():
    bad.append(f"missing holdout: {hold}")
if os.environ.get("ROTATE_EVOLVE_TASKS") == "1":
    try:
        import pandas  # noqa: F401
    except ImportError:
        bad.append("pandas is required to sample the taxonomy (ROTATE_EVOLVE_TASKS=1)")
    if not (repo / "scripts/tmax/build_iter_evolve_tasks.py").is_file():
        bad.append("missing scripts/tmax/build_iter_evolve_tasks.py")

def ids(path):
    raw = json.loads(Path(path).read_text())
    items = raw
    if isinstance(raw, dict):
        items = raw.get("tasks") or raw.get("task_ids") or []
    out = []
    for x in items or []:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            tid = x.get("task_id") or x.get("name") or x.get("id")
            if tid:
                out.append(str(tid))
    return set(out)

if not bad:
    seed_ids, exc_ids, hold_ids, old_ids = ids(seed), ids(exc), ids(hold), ids(old)
    if len(seed_ids) != 50:
        bad.append(f"alt evolve seed has {len(seed_ids)} tasks, expected 50")
    leak_h = seed_ids & hold_ids
    leak_o = seed_ids & old_ids
    leak_e = seed_ids & exc_ids
    if leak_h:
        bad.append(f"alt seed overlaps holdout-102: {len(leak_h)}")
    if leak_o:
        bad.append(f"alt seed overlaps original evolve-50: {len(leak_o)}")
    if leak_e:
        bad.append(f"alt seed overlaps extra-exclude (prior evolve ids): {len(leak_e)}")
    print(f"alt seed n={len(seed_ids)}  extra-exclude n={len(exc_ids)}  "
          f"holdout n={len(hold_ids)}")
if bad:
    print("\n".join("  - " + b for b in bad), file=sys.stderr)
    sys.exit(1)
print("preflight OK")
PY

"$PYTHON_BIN" - <<'PY' || echo "WARN: bedrock preflight failed"
import boto3, os
rt = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION_NAME", "us-west-2"))
print("bedrock-runtime OK", rt.meta.region_name)
PY

exec bash "$REPO/scripts/tmax/run_loop_tmax_coevolve.sh"
