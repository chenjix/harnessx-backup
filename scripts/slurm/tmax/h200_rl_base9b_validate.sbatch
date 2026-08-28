#!/bin/bash
# Does RL learn anything at all? Qwen3.5-9B base, no SFT, one question only.
#
#   bash scripts/tmax/prep_rl_smoke.sh   # ON A LOGIN NODE FIRST — see below
#   sbatch scripts/slurm/tmax/h200_rl_base9b_validate.sbatch
#
# This is a VALIDATION run, not a training run. Success is "grad_norm > 0 on a
# decent fraction of steps", not a better model. Nothing here has produced that
# yet: of 13 prior RL jobs the only COMPLETED one (32121) reported
# grad_norm 0.00e+00 on all four of its steps and saved a checkpoint that was
# numerically its own initial weights.
#
# What that run got wrong, and what this one changes:
#
#   * 20 agent steps per episode, against an eval harness that uses 80. A
#     terminal task that needs 30 steps scores 0 in 20 every single time, and a
#     task set that never scores makes GRPO's advantage identically 0 — which is
#     exactly the grad_norm 0 that was observed. RL_MAX_STEPS=50 here.
#
#   * 2 unique prompts x 4 samples. GRPO's advantage is reward minus the GROUP
#     mean, so learning needs prompts whose samples DISAGREE. Two prompts is too
#     few draws to land one in that band. 8 x 8 here.
#
#   * A 4B model on tasks drawn at random. This uses the 9B base and prefers the
#     nine tasks that model has actually been observed solving under the eval
#     harness (tasks_rl_known_solvable.json, measured in screening job 33602).
#     Known-solvable is the point: a task the model NEVER solves and a task it
#     ALWAYS solves both contribute zero advantage, and only tasks it sometimes
#     solves carry signal.
#
# Base model on purpose: RL_INIT_MODEL becomes the Hub id Qwen/Qwen3.5-9B rather
# than a local merged-SFT directory. grpo_fast.py:3223 pre-downloads the model
# with snapshot_download(), which rejects a local path outright
# (HFValidationError) — the bug that killed RL inside the coevolve pipeline. The
# base-model path does not touch it, so this run tests RL and nothing else.
#
#SBATCH --job-name=tmax-rl-b9b
#SBATCH --account=low-pri
#SBATCH --partition=ml.p5en.48xlarge-low
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=400G
# Sized to the run, not to the node. `--mem=0` claims ALL of a node's memory, so
# the job cannot be placed on a partially-occupied one — and every node in this
# partition is `mix`. With --mem=0 slurm quoted the same start time two days out
# for 8, 4 and 2 GPUs alike, which is the tell: memory was the binding request,
# not GPUs. (The same trap is documented in a100_tmax_coevolve.sbatch.)
#SBATCH --time=08:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tmax_rl_base9b_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tmax_rl_base9b_%j.err
#
# H200 (141G) rather than the p4d A100s every prior RL job used. ZeRO-3 puts
# roughly (18 params + 18 grads + 72 Adam)/N_LEARNERS GB on each learner for a
# 9B; at 4 learners that is ~27G, which fits an A100-40G only barely and leaves
# nothing for activations. Override for p4d with:
#   sbatch --account=interactive-ai-p4d --partition=ml.p4d.24xlarge \
#          --gres=gpu:a100:8 --export=ALL,RL_N_LEARNERS=6,RL_N_VLLM=2 ...

set -Eeuo pipefail
REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

echo "===== job ====="
echo "job   : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node  : $(hostname)"
echo "start : $(date -u '+%F %T UTC')"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
export RL_PYTHON="${RL_PYTHON:-$REPO/tmax/training/open-instruct/.venv/bin/python}"

# ── the model under test ────────────────────────────────────────────────────
export MODEL_SIZE="${MODEL_SIZE:-9b}"          # -> Qwen/Qwen3.5-9B (a Hub id)
unset ADAPTER_DIR || true                      # no SFT: RL from the base model
export RL_TOOL_PARSER="${RL_TOOL_PARSER:-vllm_qwen3_xml}"
# train_rl_grpo.sh's mismatch warning exempts qwen3.5/3.6 from the Hermes rule,
# so the xml parser is the right pairing here. A mismatch shows up as
# bash/avg_calls_per_rollout = 0; the verdict script calls that out by name.

export SMOKE_NAME="${SMOKE_NAME:-tmax_rl_base9b}"
export RL_DATASET_NAME="${RL_DATASET_NAME:-tmax_rl_base9b_data}"
export RL_OUTPUT_DIR="${RL_OUTPUT_DIR:-$REPO/outputs/rl/${SMOKE_NAME}_${SLURM_JOB_ID}}"
export RL_EXP_NAME="${RL_EXP_NAME:-tmax_rl_base9b_${SLURM_JOB_ID}}"

# ── shape: sized for reward VARIANCE, not for throughput ────────────────────
# grpo_fast's constraints:  unique_prompts >= vllm_engines
#                           unique_prompts * samples >= learners / seq_parallel
export RL_N_TASKS="${RL_N_TASKS:-32}"
export RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-8}"
export RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-8}"
export RL_EPISODES="${RL_EPISODES:-2048}"      # 8*8=64/step -> ~32 optimizer steps
export RL_MAX_STEPS="${RL_MAX_STEPS:-50}"      # agent steps per episode (was 20)
export RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-4096}"
export RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-16384}"
export RL_LR="${RL_LR:-1e-6}"
export RL_N_LEARNERS="${RL_N_LEARNERS:-4}"
export RL_N_VLLM="${RL_N_VLLM:-4}"
export RL_GPUS="${RL_GPUS:-0,1,2,3,4,5,6,7}"
export RL_SEED="${RL_SEED:-42}"
# Keep zero-std filtering ON. It drops prompt groups whose samples all scored
# alike, which is the correct behaviour — those carry no gradient either way.
# How MUCH it filters is the diagnostic: everything filtered means the task set
# sits outside this model's band entirely.
export RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-1}"

export CLEAN_STALE="${CLEAN_STALE:-1}"
export SWERL_DOCKER_AUTO_REMOVE="${SWERL_DOCKER_AUTO_REMOVE:-1}"

echo "===== plan ====="
echo "  model        : Qwen3.5-${MODEL_SIZE} BASE (no adapter)"
echo "  tasks        : $RL_N_TASKS  (dataset $RL_DATASET_NAME)"
echo "  rollout      : ${RL_UNIQUE_PROMPTS} prompts x ${RL_SAMPLES_PER_PROMPT} samples = $((RL_UNIQUE_PROMPTS*RL_SAMPLES_PER_PROMPT))/step"
echo "  episodes     : $RL_EPISODES  -> ~$((RL_EPISODES/(RL_UNIQUE_PROMPTS*RL_SAMPLES_PER_PROMPT))) optimizer steps"
echo "  agent steps  : $RL_MAX_STEPS per episode"
echo "  gpus         : ${RL_N_LEARNERS} learners + ${RL_N_VLLM} vLLM"
echo "  output       : $RL_OUTPUT_DIR"

# ── preflight ───────────────────────────────────────────────────────────────
[[ -x "$RL_PYTHON" ]] || { echo "ERROR: RL venv missing: $RL_PYTHON (scripts/tmax/setup_rl_env.sh)" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "ERROR: docker unavailable on $(hostname)" >&2; exit 2; }

DATA_DIR="$REPO/recipe/tb2_sft/data/$RL_DATASET_NAME"
if [[ ! -f "$DATA_DIR/train.jsonl" ]]; then
  echo "WARNING: $DATA_DIR/train.jsonl missing — the job will build the task set and"
  echo "         its container images itself, spending its first 7-10 GPU-minutes on"
  echo "         work that needs no GPU. Run scripts/tmax/prep_rl_smoke.sh on a login"
  echo "         node first next time."
fi

# Image store: the RL env builds one container per task, and on these nodes the
# effective store is /var/lib/containerd on a ~97G root volume that already sits
# near 90% for unrelated reasons. Job 32044 died with "12/16 task images missing"
# for exactly this reason.
MIN_IMAGE_GB="${MIN_IMAGE_GB:-$(( 8 + (RL_N_TASKS * 80 + 99) / 100 ))}"
MIN_IMAGE_GB="$MIN_IMAGE_GB" bash "$REPO/scripts/tmax/docker_preflight.sh" \
  || { echo "ERROR: not enough image-store room for $RL_N_TASKS task images" >&2; exit 2; }

mkdir -p /fsx/home/jixuan.chen/logs "$RL_OUTPUT_DIR"

set +e
bash "$REPO/scripts/tmax/smoke_rl_grpo.sh"
rc=$?
set -e
echo "smoke_rl_grpo.sh exit status: $rc"

# ── the actual verdict ──────────────────────────────────────────────────────
# Exit status answers "did it crash", which is not the question. Job 32060 exited
# 0 having produced zero gradient. This reads the per-step metrics and says
# whether any parameter moved.
echo
echo "===== learning verdict ====="
TRAIN_LOG="$(ls -t "$REPO"/logs/*/train_rl_grpo.log 2>/dev/null | head -1)"
for cand in "$TRAIN_LOG" "/fsx/home/jixuan.chen/logs/tmax_rl_base9b_${SLURM_JOB_ID}.out"; do
  [[ -f "$cand" ]] || continue
  echo "--- $cand"
  "$PYTHON_BIN" "$REPO/scripts/tmax/rl_learning_verdict.py" "$cand" && break
done

echo "done : $(date -u '+%F %T UTC')  (trainer rc=$rc)"
exit "$rc"
