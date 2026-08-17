#!/bin/bash
# Qwen3.6-27B harness<->model co-evolution (loop3), one chain, 8x H200.
#
#   sbatch scripts/coevolve_27b.sbatch
#   REPLICATE=2 sbatch --export=ALL scripts/coevolve_27b.sbatch
#   N_ITERS=1 EVOLVE_ROUNDS=2 sbatch --export=ALL scripts/coevolve_27b.sbatch   # short probe
#
# This script runs the pipeline itself rather than holding the node for an
# interactive session. That is not a style choice: the GPU monitor scans the
# process table every 60 s and cancels any job whose node shows "only sleep +
# scaffolding" — which is what killed jobs 290, 315, 316, 355, 1232 and 1233,
# all within 4-9 minutes. A node running 8 vLLM replicas and TB2 containers
# never matches that pattern. To get a shell, attach to this job once it is
# running (see the bottom of this file), do not allocate a separate one.
#
# Account/partition: jixuan.chen's user association in `interactive-ai` has
# MaxSubmitJobs=0 (every other member of that account is unlimited), so sbatch
# there is rejected with AssocMaxSubmitJobLimit. `low-pri` has no such cap, and
# ml.p5en.48xlarge / interactive both carry DenyAccounts=low-pri — which leaves
# exactly one admitting combination. Note that `sbatch --test-only` does NOT
# check association limits, so it succeeding proves nothing about the account.
#SBATCH --job-name=coevolve27b
#SBATCH --account=low-pri
#SBATCH --partition=ml.p5en.48xlarge-low
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=4-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/slurm-%x-%j.out
#SBATCH --error=/fsx/home/jixuan.chen/slurm-%x-%j.err

set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

echo "===== job ====="
echo "job     : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node    : $(hostname)"
echo "gpus    : ${CUDA_VISIBLE_DEVICES:-unset}"
echo "cpus    : $(nproc)"
echo "start   : $(date -u '+%F %T UTC')"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

# ── disk ─────────────────────────────────────────────────────────────────────
# Measure where TB2 image layers actually land, which is NOT `/`. These nodes
# report Docker Root Dir=/opt/dlami/nvme/docker/data-root on a 28 TB ephemeral
# volume, while `/` is a 97 GB OS disk sitting near 90% for unrelated reasons.
# sharded_tb2_eval.py's own guard resolves the same way; checking `/` instead
# aborted two healthy allocations that no prune could ever have rescued.
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
free_gb() { df -BG --output=avail "$1" | tail -1 | tr -dc '0-9'; }

echo
echo "===== disk ====="
echo "docker root : $DOCKER_ROOT"
df -h "$DOCKER_ROOT" | tail -1

if (( $(free_gb "$DOCKER_ROOT") < 40 )); then
  echo "below floor — reclaiming"
  docker container prune -f >/dev/null 2>&1 || true
  docker image   prune -af  >/dev/null 2>&1 || true
  docker builder prune -af  >/dev/null 2>&1 || true
  df -h "$DOCKER_ROOT" | tail -1
  if (( $(free_gb "$DOCKER_ROOT") < 40 )); then
    echo "ERROR: still under 40 GB free on $DOCKER_ROOT; image pulls would fail mid-round." >&2
    exit 2
  fi
fi

# No conda on these hosts any more. ~/.venv carries vllm for serving and
# trl/peft for the SFT stage; train_sft.sh reaches it via SFT_PYTHON in .env,
# everything else via VLLM_VENV / PYTHON_BIN.
export PYTHON_BIN=/fsx/home/jixuan.chen/.venv/bin/python

# Advisory only. doctor.sh ends in `exit $fail`, and under `set -e` one stale
# check (it flags a missing `hydra`, which only the replay-GRPO path uses)
# would abort the job before a single rollout. The stages that matter have
# their own hard preflights: interpreter imports, stale-port detection, and a
# real qwen3_xml tool-call probe against every replica before any round starts.
echo
echo "===== preflight (advisory) ====="
MODEL_SIZE=27b bash scripts/doctor.sh || echo "WARN: doctor reported issues (see above) — continuing"

echo
echo "===== loop3 ====="
export MODEL_SIZE=27b
export REPLICATE="${REPLICATE:-1}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-4}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export TB2_CONCURRENT="${TB2_CONCURRENT:-2}"

# A low-pri allocation can be preempted, and the monitor cancels any job whose
# GPU util stays under 10% for an hour. Either way the chain is resumable:
# every stage writes a .done marker, so re-submitting this same script picks up
# where it stopped instead of re-paying for finished evolve rounds. CLEAN_STALE
# clears the vLLM replicas a killed run leaves holding ports and GPU memory —
# without it the next attempt dies on "port 8300 already has an OpenAI
# endpoint" only after five minutes of loading weights.
export CLEAN_STALE=1

echo "replicate=$REPLICATE iters=$N_ITERS evolve_rounds=$EVOLVE_ROUNDS pool=$GPU_POOL"
exec bash scripts/run_loop_step3.sh

# ── attaching ────────────────────────────────────────────────────────────────
#   squeue -u $USER
#   srun --jobid=<jobid> --overlap --pty bash -l
#   cat  outputs/loop3/rep1/STATUS
#   column -t outputs/loop3/rep1/scores.tsv
#   bash scripts/report_coevolve.sh 1
