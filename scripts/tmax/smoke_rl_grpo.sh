#!/usr/bin/env bash
# End-to-end RL smoke AFTER an SFT LoRA adapter exists.
#
# Stages:
#   0) preflight (GPU, docker, python deps)
#   1) build tiny taxonomy RL set (exclude holdout-102)
#   2) merge SFT LoRA → full HF weights (required for open-instruct)
#   3) tiny grpo_fast / DPPO run
#   4) verify checkpoint artifacts
#
# Invoked by: scripts/slurm/tmax/h200_rl_smoke.sbatch
# Manual:
#   ADAPTER_DIR=outputs/sft/tmax_coev_rep1_i1 \
#   RL_GPUS=0,1 bash scripts/tmax/smoke_rl_grpo.sh
set -Eeuo pipefail

_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"
source "$_HX_SCRIPTS/_common.sh"

# grpo_fast needs ray+deepspeed+openenv+vllm+liger together; only the env built
# from open-instruct's own uv.lock has all of them (scripts/tmax/setup_rl_env.sh).
_OI_VENV_PY="$ROOT/tmax/training/open-instruct/.venv/bin/python"
if [[ -z "${RL_PYTHON:-}" && -x "$_OI_VENV_PY" ]]; then
  RL_PYTHON="$_OI_VENV_PY"
fi
PY="${RL_PYTHON:-${SFT_PYTHON:-${PYTHON_BIN:-$(python_bin)}}}"
export RL_PYTHON="$PY"
# Leave PYTHON_BIN/SFT_PYTHON alone: the merge step and the dataset builder run
# fine in the serving venv, and pointing them at the RL env is not required.
export PYTHON_BIN="${PYTHON_BIN:-$(python_bin)}"

SMOKE_NAME="${SMOKE_NAME:-tmax_rl_smoke}"
# The smoke deliberately runs the 4B model: it exercises the same code path as 9B
# (same env, same tool parser, same DPPO loss) with a fraction of the memory and
# rollout time. The real run sets MODEL_SIZE=9b.
export MODEL_SIZE="${MODEL_SIZE:-4b}"
RL_N_TASKS="${RL_N_TASKS:-4}"
# Shape has to satisfy grpo_fast's rollout-group constraint:
#   unique_prompts * samples_per_prompt >= num_learners / sequence_parallel_size
#   unique_prompts >= vllm_num_engines
# 4 learners + 2 engines + 2x4 = 8 seq/step clears both; RL_EPISODES=32 is then
# 4 optimizer steps, and save_freq=2 guarantees a checkpoint exists for stage 4.
RL_EPISODES="${RL_EPISODES:-32}"
RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-2}"
RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-4}"
RL_MAX_STEPS="${RL_MAX_STEPS:-6}"
RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-1024}"
RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-2048}"
RL_POOL_SIZE="${RL_POOL_SIZE:-4}"
# 8x A100-40GB: ZeRO-3 shards params+grads+Adam over the learners, so a 9B needs
# ~(18+18+72)/N GB per learner — 6 learners lands near 18GB + activations, 2
# learners does not fit at all. vLLM engines only hold weights + KV, so 2 is
# plenty for a smoke. Override with RL_N_LEARNERS / RL_N_VLLM.
RL_GPUS="${RL_GPUS:-${GPU_POOL:-0,1,2,3,4,5,6,7}}"
# Fewer learners than the 9B run needs: a smaller world_size means a smaller
# minimum rollout group, so the smoke finishes a step in 8 episodes instead of 12+.
RL_N_LEARNERS="${RL_N_LEARNERS:-4}"
RL_N_VLLM="${RL_N_VLLM:-2}"
RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-1}"
RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-3}"
# Smoke must not soft-filter away every group (tiny N → frequent zero-std).
RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-0}"
# 0 by default now: the uv-managed RL env is complete, and pip-installing into it
# can pull a torch that disagrees with the locked flash-attn/vllm wheels.
INSTALL_RL_DEPS="${INSTALL_RL_DEPS:-0}"

# Adapter is OPTIONAL. With no SFT run yet (RL-first debugging), leave it empty
# and the smoke runs DPPO straight off the base model — the merge stage is the
# only thing that needs it, and open-instruct wants full weights either way.
ADAPTER_DIR="${ADAPTER_DIR-$ROOT/outputs/sft/tmax_coev_rep1_i1}"
if [[ -n "$ADAPTER_DIR" && ! -f "$ADAPTER_DIR/adapter_config.json" ]]; then
  echo "note: no adapter at $ADAPTER_DIR — running RL from the base model" >&2
  ADAPTER_DIR=""
fi
RL_DATASET_NAME="${RL_DATASET_NAME:-${SMOKE_NAME}_data}"
RL_OUTPUT_DIR="${RL_OUTPUT_DIR:-$ROOT/outputs/rl/${SMOKE_NAME}}"
RL_MERGED_DIR="${RL_MERGED_DIR:-$ROOT/outputs/rl/merged/${SMOKE_NAME}_from_sft}"
JOB_TAG="${SLURM_JOB_ID:-local}"
export RUN_TAG="${RUN_TAG:-rl-smoke-${SMOKE_NAME}-${JOB_TAG}}"
export LOG_ROOT="${LOG_ROOT:-$ROOT/logs/$RUN_TAG}"
mkdir -p "$LOG_ROOT"

log() { printf '\n\033[1m[rl-smoke %s] %s\033[0m\n' "$JOB_TAG" "$*"; }
die() { echo "ERROR: $*" >&2; exit 2; }

log "plan"
echo "  python      : $PY"
echo "  adapter     : ${ADAPTER_DIR:-<none, base model>}"
echo "  merged      : $RL_MERGED_DIR"
echo "  dataset     : $RL_DATASET_NAME (n=$RL_N_TASKS)"
echo "  model       : Qwen3.5-${MODEL_SIZE}"
echo "  episodes    : $RL_EPISODES  group=${RL_UNIQUE_PROMPTS}x${RL_SAMPLES_PER_PROMPT} learners=${RL_N_LEARNERS} engines=${RL_N_VLLM}"
echo "  gpus        : $RL_GPUS"
echo "  output      : $RL_OUTPUT_DIR"
nvidia-smi -L || true

# ── 0) preflight ────────────────────────────────────────────────────────────
log "stage0 preflight"
command -v docker >/dev/null || die "docker binary missing"
docker info >/dev/null 2>&1 || die "docker not usable (daemon / permissions)"
if [[ -n "$ADAPTER_DIR" ]]; then
  ls "$ADAPTER_DIR"/adapter_model.safetensors >/dev/null 2>&1 \
    || ls "$ADAPTER_DIR"/adapter_model.bin >/dev/null 2>&1 \
    || die "SFT adapter has no weights under $ADAPTER_DIR"
fi
[[ -f "$ROOT/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet" ]] \
  || die "taxonomy parquet missing (see docs/DATA.md)"
[[ -d "$ROOT/tmax/training/open-instruct/open_instruct" ]] \
  || die "open-instruct missing under tmax/training/open-instruct"

if [[ "$INSTALL_RL_DEPS" == "1" ]]; then
  log "installing missing RL python deps (ray/deepspeed/openenv-core/docker)"
  "$PY" - <<'PY'
import importlib.util as u, subprocess, sys
need = []
for mod, pip in [
    ("ray", "ray[default]>=2.9"),
    ("deepspeed", "deepspeed>=0.14"),
    ("openenv", "openenv-core>=0.2.1"),
    ("docker", "docker>=7.0"),
]:
    if u.find_spec(mod) is None:
        need.append(pip)
if need:
    print("pip install:", need)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *need])
else:
    print("all critical RL deps already present")
PY
fi

"$PY" - <<'PY'
import importlib
missing=[]
for m in ("ray","deepspeed","datasets","transformers","torch","vllm","peft","docker"):
    try: importlib.import_module(m)
    except Exception as e: missing.append(f"{m}({e})")
# openenv.core is the import path for openenv-core
try:
    import openenv.core  # noqa: F401
except Exception as e:
    missing.append(f"openenv.core({e})")
if missing:
    raise SystemExit(
        "missing modules: " + ", ".join(missing)
        + "\n       Build the RL env from open-instruct's lockfile:\n"
        + "         bash scripts/tmax/setup_rl_env.sh\n"
        + "       then re-run with RL_PYTHON=<open-instruct>/.venv/bin/python"
    )
print("deps OK")
PY

# ── 1) dataset ──────────────────────────────────────────────────────────────
log "stage1 build RL dataset"
rm -rf "$ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME"
"$PY" -m recipe.tb2_sft.src.build_tmax_rl_dataset \
  --from-taxonomy \
  --n-tasks "$RL_N_TASKS" \
  --seed 42 \
  --name "$RL_DATASET_NAME" \
  --exclude-tasks "$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json" \
  --prefer-tasks "$ROOT/recipe/tb2_evolver/tasks_tmax_evolve50_list.json"

"$PY" - <<PY
import json
from pathlib import Path
root = Path("$ROOT")
s = json.loads((root / "recipe/tb2_sft/data/$RL_DATASET_NAME/summary.json").read_text())
assert s["n_tasks"] == int("$RL_N_TASKS"), s
assert Path(s["train_jsonl"]).is_file()
td = Path(s["task_data_dir"])
assert td.is_dir()
tid = s["task_ids"][0]
assert (td / tid / "instruction.md").is_file()
assert (td / tid / "tests" / "test.sh").is_file()
# holdout leak already enforced by builder; double-check ids look like task_*
assert all(str(t).startswith("task_") for t in s["task_ids"])
print("dataset OK", s["n_tasks"], "domains", s.get("by_domain"))
PY

# ── 1b) per-task images ─────────────────────────────────────────────────────
# swerl_vanillux_sandbox boots env_config.image and never runs setup.sh, so the
# task environment has to be inside the image. Same content-hash tags the eval
# path uses, so anything already built is skipped.
log "stage1b build per-task sandbox images"
SHARED_BASE="${SHARED_BASE:-1}" JOBS="${IMAGE_JOBS:-6}" \
  ENVS_JSONL="$ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME/eval_task_set_with_envs.jsonl" \
  bash "$ROOT/scripts/tmax/prebuild_tmax_images.sh"

# ── 2) merge SFT → full weights (only when an adapter was given) ────────────
if [[ -n "$ADAPTER_DIR" ]]; then
  log "stage2 merge SFT LoRA → full weights"
  rm -rf "$RL_MERGED_DIR"
  BASE_MODEL="${BASE_MODEL:-$MODEL}" ADAPTER_DIR="$ADAPTER_DIR" OUTPUT_DIR="$RL_MERGED_DIR" \
    bash "$ROOT/scripts/tmax/merge_sft_adapter.sh"
  [[ -f "$RL_MERGED_DIR/config.json" ]] || die "merge failed: no config.json in $RL_MERGED_DIR"
  [[ -f "$RL_MERGED_DIR/model.safetensors" || -f "$RL_MERGED_DIR/model.safetensors.index.json" ]] \
    || die "merge failed: no model weights in $RL_MERGED_DIR"
  echo "merged OK → $RL_MERGED_DIR"
  SMOKE_INIT_MODEL="$RL_MERGED_DIR"
else
  log "stage2 skipped (no adapter) — init from base $MODEL"
  SMOKE_INIT_MODEL="${BASE_MODEL:-$MODEL}"
fi

# ── 3) tiny DPPO ────────────────────────────────────────────────────────────
log "stage3 grpo_fast / DPPO"
rm -rf "$RL_OUTPUT_DIR"
export RL_DATASET_NAME
export RL_OUTPUT_DIR
export RL_INIT_MODEL="$SMOKE_INIT_MODEL"
export ADAPTER_DIR=""   # already merged; do not re-merge
export RL_N_TASKS RL_EPISODES RL_UNIQUE_PROMPTS RL_SAMPLES_PER_PROMPT
export RL_MAX_STEPS RL_PER_TURN_MAX_TOKENS RL_RESPONSE_LENGTH RL_POOL_SIZE
export RL_GPUS RL_ASYNC_STEPS RL_DEEPSPEED_STAGE RL_N_LEARNERS RL_N_VLLM
export RL_FILTER_ZERO_STD
export RL_EXP_NAME="${SMOKE_NAME}"
export RL_SAVE_FREQ="${RL_SAVE_FREQ:-2}"
export RL_CKPT_FREQ="${RL_CKPT_FREQ:-2}"
export RL_SYSTEM_PROMPT_FILE="${RL_SYSTEM_PROMPT_FILE:-$ROOT/tmax/training/open-instruct/scripts/train/debug/envs/swerl_vanillux_sandbox_system_prompt.txt}"

bash "$ROOT/scripts/tmax/train_rl_grpo.sh"

# ── 4) verify ───────────────────────────────────────────────────────────────
log "stage4 verify checkpoint"
"$PY" - <<PY
from pathlib import Path
out = Path("$RL_OUTPUT_DIR")
assert out.is_dir(), out
# open-instruct may write final weights at out/ or out/step_* /
cands = list(out.rglob("config.json"))
assert cands, f"no config.json under {out}"
# at least some training log
log = Path("$ROOT/logs") 
print("ckpt configs:", [str(p.relative_to(out)) for p in cands[:8]])
print("RL smoke artifacts OK")
PY

# Prefer a non-empty train log
if [[ -f "$LOG_ROOT/train_rl_grpo.log" ]]; then
  rg -n 'loss|episode|reward|Saving|saved' "$LOG_ROOT/train_rl_grpo.log" | tail -20 || true
fi

log "PASSED"
echo "  adapter (SFT) : ${ADAPTER_DIR:-<none, base model>}"
echo "  init model    : $SMOKE_INIT_MODEL"
echo "  dataset       : $ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME"
echo "  rl ckpt       : $RL_OUTPUT_DIR"
ls -la "$RL_OUTPUT_DIR" | head -25
