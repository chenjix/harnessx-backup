#!/bin/bash
#SBATCH --job-name=tb21-rlbest-89
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=1-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tb21_rlbest_89_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tb21_rlbest_89_%j.err

set -Eeuo pipefail
REPO="${REPO:-/fsx/home/jixuan.chen/harnessx-backup}"
cd "$REPO"
mkdir -p /fsx/home/jixuan.chen/logs
exec bash "$REPO/scripts/eval_rep46_rl_best_tb21.sh"
