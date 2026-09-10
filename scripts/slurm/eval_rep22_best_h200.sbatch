#!/bin/bash
#SBATCH --job-name=rep22-best-eval
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=1-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/rep22_best_eval_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/rep22_best_eval_%j.err

set -Eeuo pipefail
REPO="${REPO:-/fsx/home/jixuan.chen/harnessx-backup}"
cd "$REPO"
mkdir -p /fsx/home/jixuan.chen/logs

# TARGET=tmax, tb21, or both.  TB21_HARNESS_MODE=native is the fair transfer;
# exact evaluates the unmodified Tmax YAML and is useful as a domain-shift check.
export TARGET="${TARGET:-both}"
export TB21_HARNESS_MODE="${TB21_HARNESS_MODE:-native}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"

exec bash "$REPO/scripts/eval_rep22_best.sh"
