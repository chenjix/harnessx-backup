#!/bin/bash
# Gate the RL init checkpoint: static audit → re-merge LoRA → vLLM boot.
#
# This is the cheap job to run BEFORE h200_rl_smoke.sbatch / dryrun_rl_grpo.sh.
# The rl dry-runs died in vLLM engine init with
#   AttributeError: 'Qwen3_5TextConfig' object has no attribute 'vision_config'
# only after ray and a 4-actor sandbox pool were up, leaving an empty output
# dir. Qwen3.5 is a VLM shell and vLLM 0.24 registers only
# Qwen3_5ForConditionalGeneration, so a bare merged text tower is unservable.
# This job proves the checkpoint loads before any RL machinery starts.
#
# Submit:
#   sbatch /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tmax/h200_rl_ckpt_smoke.sbatch
#
# Audit an existing checkpoint without re-merging:
#   sbatch --export=ALL,REMERGE=0,CKPT_DIR=/path/to/merged \
#     scripts/slurm/tmax/h200_rl_ckpt_smoke.sbatch
#
#SBATCH --job-name=tmax-rl-ckpt
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:1
#SBATCH --cpus-per-task=24
#SBATCH --mem=0
#SBATCH --time=0-02:00:00
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_rl_ckpt_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_rl_ckpt_%j.err

set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"
mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/rl/merged"

echo "===== job ====="
echo "job     : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node    : $(hostname)"
echo "gpus    : ${CUDA_VISIBLE_DEVICES:-unset}"
echo "start   : $(date -u '+%F %T UTC')"
nvidia-smi -L || true

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export SFT_PYTHON="${SFT_PYTHON:-$PYTHON_BIN}"
export RL_PYTHON="${RL_PYTHON:-$PYTHON_BIN}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"

# Same adapter h200_rl_smoke.sbatch warm-starts from.
export ADAPTER_DIR="${ADAPTER_DIR:-$REPO/outputs/sft/tmax_coev_rep1_i1}"
# The checkpoint under test. Default is the stale text-only merge the dry-runs
# reused, so a plain submit reproduces the failure and then rebuilds it.
export CKPT_DIR="${CKPT_DIR:-$REPO/outputs/rl/merged/tmax_rl_smoke_1948}"
export REMERGE="${REMERGE:-1}"
export REMERGE_OUT="${REMERGE_OUT:-$REPO/outputs/rl/merged/$(basename "$ADAPTER_DIR")_shell}"
# Cheap on a compute node, and it is the only check that catches a no-op merge.
export DEEP="${DEEP:-1}"
export SKIP_BOOT="${SKIP_BOOT:-0}"
export BOOT_MAX_LEN="${BOOT_MAX_LEN:-4096}"
export BOOT_UTIL="${BOOT_UTIL:-0.40}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

if [[ "$CLEAN_STALE" == "1" ]]; then
  pkill -f 'vllm.entrypoints.openai.api_server' 2>/dev/null || true
  ray stop --force >/dev/null 2>&1 || true
  sleep 3
fi

echo "===== ckpt smoke plan ====="
echo "  CKPT_DIR    : $CKPT_DIR"
echo "  ADAPTER_DIR : $ADAPTER_DIR"
echo "  REMERGE     : $REMERGE  (out=$REMERGE_OUT)"
echo "  DEEP        : $DEEP"
echo "  SKIP_BOOT   : $SKIP_BOOT"

exec bash "$REPO/scripts/tmax/smoke_rl_ckpt.sh"
