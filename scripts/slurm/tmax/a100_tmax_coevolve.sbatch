#!/bin/bash
# Tmax harness ↔ SFT co-evolution (multi-iter), one chain, 8× A100-40GB (p4d).
#
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/a100_tmax_coevolve.sbatch
#   REPLICATE=2 sbatch --export=ALL .../a100_tmax_coevolve.sbatch
#
# Smoke (1 iter, 2 evolve rounds, no retries):
#   sbatch --export=ALL,N_ITERS=1,EVOLVE_ROUNDS=2,MAX_SFT_RETRIES=0 \
#     .../a100_tmax_coevolve.sbatch
#
# Warm-start from a proven adapter:
#   sbatch --export=ALL,INIT_LORA_PATH=/fsx/home/jixuan.chen/harnessx-backup/outputs/sft/qwen35_9b_tmax_only500_on102_v2 \
#     .../a100_tmax_coevolve.sbatch
#
# Differences from the H200 sbatch, all driven by 40GB/GPU instead of 141GB:
#   * SFT_MAX_SEQ_LENGTH 16384 → 8192 (LoRA + DDP activations at bs=1 do not fit
#     at 16k on 40GB once the 9B weights take ~18.5GB).
#   * GPU_MEMORY_UTILIZATION pinned at 0.88, MAX_MODEL_LEN at 32768 — the KV
#     cache that leaves is still ~100k tokens, far past what one rollout uses.
#   * ENABLE_RL stays 0: the open-instruct DPPO path wants a vLLM replica *and*
#     trainer shards on the same 40GB card.
# bf16 and flash_attention_2 are both fine on sm80, so the training config is
# otherwise identical.
#
#SBATCH --job-name=tmax-coevolve-a100
#SBATCH --account=interactive-ai-p4d
#SBATCH --partition=ml.p4d.24xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:a100:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=4-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_coevolve_a100_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_coevolve_a100_%j.err

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
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
# The vLLM venv has no trl/peft; training uses the conda env that does.
_SFT_PY_DEFAULT=/fsx/home/jixuan.chen/miniconda3/envs/vllm-019-cu128-clean/bin/python
[[ -x "$_SFT_PY_DEFAULT" ]] || _SFT_PY_DEFAULT="$PYTHON_BIN"
export SFT_PYTHON="${SFT_PYTHON:-$_SFT_PY_DEFAULT}"
export SFT_ATTN_IMPLEMENTATION="${SFT_ATTN_IMPLEMENTATION:-sdpa}"   # no flash-attn in that env
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export META_MODEL="${META_MODEL:-openai/gpt-5.5}"
export PROVIDER_ID="${PROVIDER_ID:-openai}"
export GATEWAY_URL="${GATEWAY_URL:-https://gateway.salesforceresearch.ai}"
export SFG_API_KEY="${SFG_API_KEY:-${OPENAI_API_KEY:-}}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-$SFG_API_KEY}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"
export SHARED_BASE="${SHARED_BASE:-0}"   # see scripts/tmax/prebuild_tmax_images.sh
if [[ "$SHARED_BASE" == "1" ]]; then
  export TMAX_SHARED_BASE_TAG="${TMAX_SHARED_BASE_TAG:-hx-tmax-base:1}"
fi

# ── 40GB serving / training headroom ────────────────────────────────────────
export MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.88}"
export SFT_MAX_SEQ_LENGTH="${SFT_MAX_SEQ_LENGTH:-8192}"
export SFT_PER_DEVICE_BS="${SFT_PER_DEVICE_BS:-1}"
export SFT_GRAD_CHECKPOINT="${SFT_GRAD_CHECKPOINT:-true}"
export SFT_EFFECTIVE_BATCH="${SFT_EFFECTIVE_BATCH:-32}"

export REPLICATE="${REPLICATE:-1}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}"
export MIN_TRAJS="${MIN_TRAJS:-20}"
export MAX_TRAJS="${MAX_TRAJS:-100}"
export PER_TASK="${PER_TASK:-3}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-8}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-2}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export CORPUS_PREFER_CURRENT="${CORPUS_PREFER_CURRENT:-1}"
export TMAX_MAX_TOKENS="${TMAX_MAX_TOKENS:-4096}"

# ── ratchet: accept a tie when the candidate's own holdout run was clean ─────
export ACCEPT_TIES="${ACCEPT_TIES:-1}"
export TIE_MAX_SYSTEM_ERRORS="${TIE_MAX_SYSTEM_ERRORS:-0}"
export SYSTEM_ERROR_STATUSES="${SYSTEM_ERROR_STATUSES:-error,agent_error}"
export MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"
export EVAL_HARNESS_AFTER_EXTRA="${EVAL_HARNESS_AFTER_EXTRA:-0}"

# ── rotate the evolve set every iteration ───────────────────────────────────
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export ROTATE_FROM_ITER="${ROTATE_FROM_ITER:-2}"
export EVOLVE_SET_SIZE="${EVOLVE_SET_SIZE:-50}"
export ROTATE_MIN_SET_SIZE="${ROTATE_MIN_SET_SIZE:-20}"
export ROTATE_SEED="${ROTATE_SEED:-42}"
export MASTERY_MIN_SUCCESSES="${MASTERY_MIN_SUCCESSES:-1}"
export MASTERY_MIN_SUCCESS_RATE="${MASTERY_MIN_SUCCESS_RATE:-0}"
export MASTERY_REQUIRE_CORPUS="${MASTERY_REQUIRE_CORPUS:-1}"
export TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$REPO/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"
# outputs/ and data/ are gitignored, so a fresh clone has no parquet. Fall back to
# the copy in the older working tree rather than failing the rotation preflight.
_TAXONOMY_FALLBACK=/fsx/home/jixuan.chen/qwen35-tb2-fullstack/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet
if [[ ! -f "$TAXONOMY_PARQUET" && -f "$_TAXONOMY_FALLBACK" ]]; then
  echo "NOTE: $TAXONOMY_PARQUET missing — using $_TAXONOMY_FALLBACK"
  export TAXONOMY_PARQUET="$_TAXONOMY_FALLBACK"
fi
# Optional extra env-row sources for newly drawn tasks (space separated). The
# taxonomy parquet already carries container_def, so this is normally empty.
export POOL_ENVS_JSONL="${POOL_ENVS_JSONL:-}"

# open-instruct DPPO/GRPO after SFT — leave off on 40GB cards.
export ENABLE_RL="${ENABLE_RL:-0}"
export RL_EPISODES="${RL_EPISODES:-512}"
export RL_N_TASKS="${RL_N_TASKS:-100}"

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/tmax_coevolve"

echo "===== plan ====="
echo "  REPLICATE          : $REPLICATE"
echo "  N_ITERS            : $N_ITERS"
echo "  EVOLVE_ROUNDS      : $EVOLVE_ROUNDS"
echo "  EXTRA_EVOLVE_ROUNDS: $EXTRA_EVOLVE_ROUNDS"
echo "  MAX_SFT_RETRIES    : $MAX_SFT_RETRIES"
echo "  MIN/MAX_TRAJS      : $MIN_TRAJS / $MAX_TRAJS"
echo "  INIT_LORA_PATH     : ${INIT_LORA_PATH:-<base>}"
echo "  SEED_HARNESS       : ${SEED_HARNESS:-configs/baseline_tmax_harness.yaml}"
echo "  META_MODEL         : $META_MODEL"
echo "  ratchet            : ACCEPT_TIES=$ACCEPT_TIES TIE_MAX_SYSTEM_ERRORS=$TIE_MAX_SYSTEM_ERRORS HARNESS_RATCHET=$HARNESS_RATCHET"
echo "  rotation           : ROTATE_EVOLVE_TASKS=$ROTATE_EVOLVE_TASKS from iter $ROTATE_FROM_ITER size $EVOLVE_SET_SIZE"
echo "  task pool          : $TAXONOMY_PARQUET${POOL_ENVS_JSONL:+ + $POOL_ENVS_JSONL}"
echo "  ENABLE_RL          : $ENABLE_RL  (RL train n=$RL_N_TASKS; holdout=eval only)"
echo "  SFT seq/bs         : $SFT_MAX_SEQ_LENGTH / $SFT_PER_DEVICE_BS (eff $SFT_EFFECTIVE_BATCH)"
echo "  vLLM               : max_model_len=$MAX_MODEL_LEN util=$GPU_MEMORY_UTILIZATION"

# Docker is required for Tmax task envs — fail here rather than mid-round.
docker info >/dev/null 2>&1 || { echo "ERROR: docker unavailable on $(hostname)" >&2; exit 2; }

# Meta-agent preflight: SigV4 for bedrock/*, gateway key for everything else.
case "$META_MODEL" in
  bedrock/*)
    "$PYTHON_BIN" - <<'PY' || echo "WARN: bedrock preflight failed"
import boto3, os
rt = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION_NAME", "us-west-2"))
print("bedrock-runtime OK", rt.meta.region_name)
PY
    ;;
  *)
    [[ -n "$SFG_API_KEY" ]] || { echo "ERROR: META_MODEL=$META_MODEL needs SFG_API_KEY (or OPENAI_API_KEY)" >&2; exit 2; }
    echo "meta-agent: $META_MODEL via $GATEWAY_URL (key len ${#SFG_API_KEY})"
    ;;
esac

exec bash "$REPO/scripts/tmax/run_loop_tmax_coevolve.sh"
