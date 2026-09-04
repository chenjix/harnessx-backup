#!/bin/bash
# Tmax harness <-> SFT co-evolution on Qwen3.6-27B, NO RL, one chain, 8x H200.
#
# Companion to h200_tmax_coevolve_norl.sbatch (Qwen3.5-9B). Every experiment knob
# is deliberately identical so the two chains form a clean model-size ablation;
# the only intended difference is MODEL_SIZE. Do not "tune" one side only.
#
# What 27b changes, and what it does not:
#   MODEL_SIZE=27b   -> _common.sh resolves Qwen/Qwen3.6-27B and MODEL_TAG=qwen36,
#                       so run tags / adapter dirs are not mislabelled as 3.5 data
#   same architecture -> Qwen3_5ForConditionalGeneration with a vision_config, i.e.
#                       the same VLM shell as the 9b, so the qwen3_xml tool parser,
#                       the base+LoRA serving rule and the LoRA target modules all
#                       apply unchanged
#   serving           -> still one vLLM replica per GPU, no tensor parallelism:
#                       27b bf16 is ~54 GB and gpu_memory_utilization=0.90 leaves
#                       ~129 GB on an H200, so ~75 GB remains for KV cache
#   task env files    -> the *_envs.jsonl paths below live under a directory named
#                       qwen35_9b_* purely because of how they were first built.
#                       They contain task/container definitions only (no model
#                       field), so they are shared, not copied per model size.
#
# Runtime: 27b decoding is bandwidth-bound on ~3x the weights, so inference stages
# run ~2-3x slower than the 9b chain. Three iterations land around 49-73 h with no
# top-ups and 58-86 h with one top-up each, but a chain that also spends SFT
# retries can exceed the 96 h slot. That is survivable, not fatal: every stage
# writes a .done marker under outputs/tmax_coevolve/rep<N>/, so re-submitting this
# exact script resumes instead of re-paying for finished work.
#
# Submit a fresh 3-iteration chain:
#   sbatch --export=ALL,REPLICATE=27,N_ITERS=3 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_norl_27b.sbatch
#
# Resume it after a timeout or preemption (same command, .done markers skip ahead):
#   sbatch --export=ALL,REPLICATE=27,N_ITERS=3 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_norl_27b.sbatch
#
# Shape check (~18 h, not cheap: it still pays three 102-task holdout evals for
# the anchor, the B stage and the E stage):
#   sbatch --export=ALL,REPLICATE=28,N_ITERS=1,EVOLVE_ROUNDS=2,MAX_SFT_RETRIES=0,MAX_TOPUP_ROUNDS=0 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_tmax_coevolve_norl_27b.sbatch
#
#SBATCH --job-name=tmax-coev-27b
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=4-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coev_27b_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coev_27b_%j.err

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
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

# ── the one intended difference from the 9b chain ───────────────────────────
export MODEL_SIZE=27b

# Separate replicate namespace so 27b state never lands in a 9b chain's
# outputs/tmax_coevolve/rep<N>/ directory.
export REPLICATE="${REPLICATE:-27}"
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
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-2}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"

# LoRA SFT on 27b: DDP keeps a full bf16 copy per rank (~56 GB of 143 GB) and the
# loss upcasts a seq x 248320 logit tensor, so this looks tight — but with
# packing off and per-device batch 1, memory tracks the actual sample length, not
# this cap. Measured over the existing Tmax corpora the median sample is ~1.4k
# tokens, p99 ~8k and the longest 11.1k, with nothing above 16384; at 11.1k the
# peak works out to ~85-90 GB. So the cap never binds and lowering it would only
# truncate the longest ~1% of trajectories while breaking comparability with the
# 9b chain. Left at the 9b value deliberately.
export SFT_MAX_SEQ_LENGTH="${SFT_MAX_SEQ_LENGTH:-16384}"

# ── ratchet: keep the floor rising, but let genuine ties through ────────────
# A flat holdout score is accepted only when the run is clean: agent_error may
# not exceed the incumbent's by more than SYSERR_TOL, and TIE_MAX_SYSERR caps it
# in absolute terms so a tie cannot inherit the incumbent's error budget.
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

# Task/container definitions, shared with the 9b chain (see header note on the
# qwen35_9b_* directory name). Pinned explicitly so a future change to the
# driver's defaults cannot silently repoint this ablation at a different set.
export HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_only200.json}"
export HOLDOUT_ENVS_JSONL="${HOLDOUT_ENVS_JSONL:-$REPO/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl}"
export EVOLVE_TASKS_JSON="${EVOLVE_TASKS_JSON:-$REPO/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
export EVOLVE_ENVS_JSONL="${EVOLVE_ENVS_JSONL:-$REPO/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl}"

# ── RL is off in this chain ────────────────────────────────────────────────
export ENABLE_RL=0
export INSTALL_RL_DEPS=0

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/tmax_coevolve"

echo "===== plan ====="
echo "  MODEL_SIZE         : $MODEL_SIZE  (Qwen/Qwen3.6-27B, MODEL_TAG=qwen36)"
echo "  REPLICATE          : $REPLICATE"
echo "  N_ITERS            : $N_ITERS"
echo "  EVOLVE_ROUNDS      : $EVOLVE_ROUNDS  (+$EXTRA_EVOLVE_ROUNDS on retry)"
echo "  MAX_SFT_RETRIES    : $MAX_SFT_RETRIES"
echo "  traj floor         : $MIN_TRAJS  (top up <=$MAX_TOPUP_ROUNDS x +$EXTRA_EVOLVE_ROUNDS rounds)"
echo "  traj cap           : $( ((MAX_TRAJS>0)) && echo "$MAX_TRAJS" || echo 'none' )   per_task=$PER_TASK"
echo "  sft max_seq_len    : $SFT_MAX_SEQ_LENGTH"
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
try:
    import pandas  # noqa: F401
except ImportError:
    bad.append("pandas is required to sample the taxonomy (ROTATE_EVOLVE_TASKS=1)")

for key in ("HOLDOUT_TASKS_JSON", "HOLDOUT_ENVS_JSONL", "EVOLVE_TASKS_JSON", "EVOLVE_ENVS_JSONL"):
    p = Path(os.environ[key])
    if not p.is_file():
        bad.append(f"missing {key}: {p}")
for rel in ("scripts/tmax/build_iter_evolve_tasks.py",
            "scripts/tmax/run_loop_tmax_coevolve.sh",
            "configs/baseline_tmax_harness.yaml"):
    if not (repo / rel).is_file():
        bad.append(f"missing {rel}")

# Resolve the 27b weights now. A missing shard discovered by rank 3 an hour into
# the first evolve round is the same failure, found much later and after the
# harness has already burned Bedrock calls.
try:
    from huggingface_hub import snapshot_download
    from transformers import AutoConfig

    path = snapshot_download("Qwen/Qwen3.6-27B")
    cfg = AutoConfig.from_pretrained(path, trust_remote_code=True)
    txt = getattr(cfg, "text_config", None) or cfg
    shards = list(Path(path).glob("*.safetensors"))
    gb = sum(s.stat().st_size for s in shards) / 1e9
    print(f"  model     : {path}")
    print(f"  arch      : {cfg.architectures} layers={getattr(txt, 'num_hidden_layers', '?')}"
          f" hidden={getattr(txt, 'hidden_size', '?')}")
    print(f"  weights   : {len(shards)} shard(s), {gb:.0f} GB")
    if getattr(cfg, "vision_config", None) is None:
        bad.append("Qwen3.6-27B config has no vision_config — the VLM-shell serving "
                   "and merge paths assume one; investigate before training")
    if gb < 40:
        bad.append(f"only {gb:.0f} GB of weight shards resolved; the snapshot looks incomplete")
except Exception as exc:  # noqa: BLE001
    bad.append(f"could not resolve Qwen/Qwen3.6-27B: {type(exc).__name__}: {exc}")

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
