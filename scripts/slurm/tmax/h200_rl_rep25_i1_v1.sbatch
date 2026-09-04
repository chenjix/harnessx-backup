#!/bin/bash
#SBATCH --job-name=rl-r25i1-v1
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=2-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/rl_rep25_i1_v1_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/rl_rep25_i1_v1_%j.err

set -Eeuo pipefail
cd /fsx/home/jixuan.chen/harnessx-backup
exec bash scripts/tmax/run_rl_rep25_i1_v1.sh
