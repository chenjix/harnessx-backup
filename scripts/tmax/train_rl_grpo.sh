#!/usr/bin/env bash
# Online Tmax RL (DPPO / GRPO-fast) after coevolve SFT.
#
# Train pool: up to RL_N_TASKS (default 100) from taxonomy parquet, always
# excluding holdout-102. Prefer keeping evolve-50 inside the pool.
#
# Usage:
#   RL_DATASET_NAME=tmax_rl_train100 \
#   ADAPTER_DIR=outputs/sft/tmax_coev_rep1_i1 \
#   RL_OUTPUT_DIR=outputs/rl/tmax_coev_rep1_i1 \
#   bash scripts/tmax/train_rl_grpo.sh
#
# Or pass an already-merged full checkpoint:
#   RL_INIT_MODEL=outputs/rl/merged/... RL_OUTPUT_DIR=... RL_DATASET_NAME=... \
#     bash scripts/tmax/train_rl_grpo.sh
set -euo pipefail

_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
source "$_HX_SCRIPTS/_common.sh"

OPEN_INSTRUCT_ROOT="${OPEN_INSTRUCT_ROOT:-$ROOT/tmax/training/open-instruct}"
[[ -d "$OPEN_INSTRUCT_ROOT/open_instruct" ]] || {
  echo "ERROR: open-instruct not found at $OPEN_INSTRUCT_ROOT" >&2
  exit 2
}

RL_DATASET_NAME="${RL_DATASET_NAME:?set RL_DATASET_NAME}"
RL_N_TASKS="${RL_N_TASKS:-100}"
RL_SEED="${RL_SEED:-42}"
HOLDOUT_TASKS_JSON="${HOLDOUT_TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_only200.json}"
PREFER_TASKS_JSON="${PREFER_TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_tmax_evolve50_list.json}"
TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$ROOT/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"
DATA_ROOT="${RL_DATA_ROOT:-$ROOT/recipe/tb2_sft/data/$RL_DATASET_NAME}"
RL_ENVS_JSONL="${RL_ENVS_JSONL:-}"
# Optional: rank/drop the RL pool from tournament-evolve pass/fail patterns.
# RL_EVOLVE_REPLICATE=22 scans .benchmarks/tmax/tmax-coev-rep22-i*-r*-traj.
RL_EVOLVE_REPLICATE="${RL_EVOLVE_REPLICATE:-}"
RL_EVOLVE_MASTERED="${RL_EVOLVE_MASTERED:-}"
RL_EVOLVE_SELECT_DIR="${RL_EVOLVE_SELECT_DIR:-$DATA_ROOT/evolve_select}"
RL_EXCLUDE_EXTRA_JSON="${RL_EXCLUDE_EXTRA_JSON:-}"

if [[ -n "$RL_EVOLVE_REPLICATE" ]]; then
  echo "Selecting RL tasks from evolve outcomes (rep=$RL_EVOLVE_REPLICATE n=$RL_N_TASKS)"
  sel_args=(
    --replicate "$RL_EVOLVE_REPLICATE"
    --n-tasks "$RL_N_TASKS"
    --out-dir "$RL_EVOLVE_SELECT_DIR"
    --holdout "$HOLDOUT_TASKS_JSON"
  )
  [[ -n "$RL_EVOLVE_MASTERED" ]] && sel_args+=(--mastered "$RL_EVOLVE_MASTERED")
  "$(python_bin)" -m recipe.tb2_sft.src.select_rl_tasks_from_evolve "${sel_args[@]}"
  PREFER_TASKS_JSON="$RL_EVOLVE_SELECT_DIR/prefer_tasks.json"
  RL_EXCLUDE_EXTRA_JSON="$RL_EVOLVE_SELECT_DIR/exclude_extra.json"
  n_sel="$("$(python_bin)" -c "import json;print(len(json.load(open('$PREFER_TASKS_JSON'))))")"
  if (( n_sel < 1 )); then
    echo "ERROR: evolve selector produced 0 tasks" >&2
    exit 2
  fi
  if (( n_sel < RL_N_TASKS )); then
    echo "NOTE: evolve select has $n_sel split tasks < RL_N_TASKS=$RL_N_TASKS — using $n_sel (no random taxonomy fill)"
    RL_N_TASKS="$n_sel"
  fi
  RL_REBUILD_DATASET=1
fi
# vanillux_instance_v1 = user message embeds COMPLETE_TASK submit boilerplate.
# Older jsonl rows were raw taxonomy text; system_prompt_override then stripped
# the only remaining submit hint, so every episode scored 0.
RL_PROMPT_SCHEMA="${RL_PROMPT_SCHEMA:-vanillux_instance_v1}"

need_build=0
if [[ "${RL_REBUILD_DATASET:-0}" == "1" ]]; then
  echo "RL_REBUILD_DATASET=1 — rebuilding $DATA_ROOT"
  need_build=1
elif [[ ! -f "$DATA_ROOT/summary.json" || ! -f "$DATA_ROOT/train.jsonl" ]]; then
  need_build=1
else
  schema="$("$(python_bin)" -c "import json;print(json.load(open('$DATA_ROOT/summary.json')).get('prompt_schema',''))" 2>/dev/null || true)"
  if [[ "$schema" != "$RL_PROMPT_SCHEMA" ]]; then
    echo "NOTE: RL dataset prompt_schema='${schema:-<missing>}' != $RL_PROMPT_SCHEMA — rebuilding"
    need_build=1
  fi
fi

if (( need_build )); then
  echo "Building RL dataset → $DATA_ROOT (n_tasks=$RL_N_TASKS, exclude holdout, schema=$RL_PROMPT_SCHEMA)"
  build_args=(
    --name "$RL_DATASET_NAME"
    --out-root "$ROOT/recipe/tb2_sft/data"
    --exclude-tasks "$HOLDOUT_TASKS_JSON"
    --n-tasks "$RL_N_TASKS"
    --seed "$RL_SEED"
    --prefer-tasks "$PREFER_TASKS_JSON"
    --env-name "${RL_ENV_NAME:-swerl_vanillux_sandbox}"
    --image-mode "${RL_IMAGE_MODE:-local}"
  )
  if [[ -n "$RL_EXCLUDE_EXTRA_JSON" && -f "$RL_EXCLUDE_EXTRA_JSON" ]]; then
    build_args+=(--exclude-extra "$RL_EXCLUDE_EXTRA_JSON")
  fi
  [[ -n "${RL_IMAGE_REGISTRY:-}" ]] && build_args+=(--image-registry "$RL_IMAGE_REGISTRY")
  if [[ -n "$RL_ENVS_JSONL" ]]; then
    build_args+=(--envs-jsonl "$RL_ENVS_JSONL")
  else
    build_args+=(--from-taxonomy --taxonomy-parquet "$TAXONOMY_PARQUET")
  fi
  "$(python_bin)" -m recipe.tb2_sft.src.build_tmax_rl_dataset "${build_args[@]}"
fi
require_file "$DATA_ROOT/summary.json"
require_file "$DATA_ROOT/train.jsonl"
TASK_DATA_DIR="$DATA_ROOT/task_data"
[[ -d "$TASK_DATA_DIR" ]] || { echo "ERROR: missing $TASK_DATA_DIR" >&2; exit 2; }

TRAIN_MIXER="$DATA_ROOT/train.jsonl"
n_tasks="$("$(python_bin)" -c "import json;print(json.load(open('$DATA_ROOT/summary.json'))['n_tasks'])")"
echo "RL train tasks: $n_tasks (mixer=$TRAIN_MIXER)"
if (( n_tasks < 1 )); then
  echo "ERROR: empty RL task set" >&2
  exit 2
fi

"$(python_bin)" - "$DATA_ROOT/summary.json" "$HOLDOUT_TASKS_JSON" <<'PY'
import json, sys
from pathlib import Path
s=json.load(open(sys.argv[1]))
raw=json.loads(Path(sys.argv[2]).read_text())
items=raw if isinstance(raw,list) else raw.get("tasks") or raw.get("task_ids") or []
hold=set()
for x in items:
    hold.add(x if isinstance(x,str) else (x.get("task_id") or x.get("name")))
leak=[t for t in s["task_ids"] if t in hold]
if leak:
    raise SystemExit(f"HOLD OUT LEAK in RL set: {leak[:8]}")
print(f"holdout-leak check OK (0 / {len(hold)} holdout ids)")
PY

# Docker images are node-local, so a set built anywhere else is not here. If the
# preparation step left an archive on shared storage, load it: unpacking locally
# takes under a minute, whereas rebuilding from the network takes minutes with the
# GPUs already reserved and doing nothing.
if [[ "${RL_SKIP_IMAGE_CHECK:-0}" != "1" && -f "$DATA_ROOT/images.tar" ]]; then
  export DATA_ROOT
  need_load=0
  while read -r tag; do
    [[ -n "$tag" ]] || continue
    docker image inspect "$tag" >/dev/null 2>&1 || need_load=1
  done < <("$(python_bin)" - <<'PY'
import json, os
p = os.path.join(os.environ["DATA_ROOT"], "train.jsonl")
print("\n".join(sorted({json.loads(l)["env_config"]["image"] for l in open(p) if l.strip()})))
PY
)
  if (( need_load )); then
    echo "loading task images from $DATA_ROOT/images.tar"
    t0=$(date +%s)
    docker load -i "$DATA_ROOT/images.tar" >/dev/null
    echo "  loaded in $(( $(date +%s) - t0 ))s"
  fi
fi

# If the archive did not cover everything, build what is left here and export the
# archive so the next run on any node just loads it. Refusing instead would mean
# the run cannot proceed at all on a node that has never seen these tasks.
if [[ "${RL_SKIP_IMAGE_CHECK:-0}" != "1" && "${RL_AUTOBUILD_IMAGES:-1}" == "1" ]]; then
  export DATA_ROOT
  n_missing="$("$(python_bin)" - <<'PY'
import json, os, subprocess
p = os.path.join(os.environ["DATA_ROOT"], "train.jsonl")
want = sorted({json.loads(l)["env_config"]["image"] for l in open(p) if l.strip()})
have = set(subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                          text=True, capture_output=True, timeout=120).stdout.split())
print(len([t for t in want if t not in have]))
PY
)"
  if (( n_missing > 0 )); then
    echo "building $n_missing missing task image(s) on this node"
    SHARED_BASE=1 JOBS="${RL_IMAGE_JOBS:-8}"       ENVS_JSONL="$DATA_ROOT/eval_task_set_with_envs.jsonl"       bash "$ROOT/scripts/tmax/prebuild_tmax_images.sh"
    if [[ ! -f "$DATA_ROOT/images.tar" ]]; then
      echo "exporting images -> $DATA_ROOT/images.tar (subsequent runs load instead of build)"
      mapfile -t _tags < <("$(python_bin)" - <<'PY'
import json, os
p = os.path.join(os.environ["DATA_ROOT"], "train.jsonl")
print("\n".join(sorted({json.loads(l)["env_config"]["image"] for l in open(p) if l.strip()})))
PY
)
      docker save -o "$DATA_ROOT/images.tar" "${_tags[@]}" ||         echo "WARNING: could not export images.tar (continuing)"
    fi
  fi
fi

# Every task boots its own image; a missing one fails that task's reset on every
# single rollout (the env raises rather than falling back). Catch it here.
if [[ "${RL_SKIP_IMAGE_CHECK:-0}" != "1" ]]; then
  "$(python_bin)" - "$DATA_ROOT/train.jsonl" <<'PY'
import json, subprocess, sys
from pathlib import Path
rows = [json.loads(l) for l in Path(sys.argv[1]).read_text().splitlines() if l.strip()]
want = sorted({r["env_config"]["image"] for r in rows})
have = set(subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                          text=True, capture_output=True, timeout=120).stdout.split())
missing = [i for i in want if i not in have]
if missing:
    raise SystemExit(
        f"ERROR: {len(missing)}/{len(want)} task image(s) missing on this node, e.g. "
        f"{missing[:3]}\n"
        "       Build them (they are the same images the eval path uses):\n"
        "         SHARED_BASE=1 ENVS_JSONL=<dataset>/eval_task_set_with_envs.jsonl \\\n"
        "           bash scripts/tmax/prebuild_tmax_images.sh\n"
        "       Or set RL_SKIP_IMAGE_CHECK=1 to launch anyway."
    )
print(f"task images OK: {len(want)} present locally")
PY
fi

# ── Init weights (merged SFT or base) ───────────────────────────────────────
RL_OUTPUT_DIR="${RL_OUTPUT_DIR:?set RL_OUTPUT_DIR}"
RL_INIT_MODEL="${RL_INIT_MODEL:-}"
ADAPTER_DIR="${ADAPTER_DIR:-}"
if [[ -z "$RL_INIT_MODEL" ]]; then
  if [[ -n "$ADAPTER_DIR" && -f "$ADAPTER_DIR/adapter_config.json" ]]; then
    RL_INIT_MODEL="${RL_MERGED_DIR:-$ROOT/outputs/rl/merged/$(basename "$ADAPTER_DIR")}"
    if [[ ! -f "$RL_INIT_MODEL/config.json" ]]; then
      BASE_MODEL="${BASE_MODEL:-$MODEL}" ADAPTER_DIR="$ADAPTER_DIR" OUTPUT_DIR="$RL_INIT_MODEL" \
        bash "$ROOT/scripts/tmax/merge_sft_adapter.sh"
    fi
  elif [[ -n "$ADAPTER_DIR" && -f "$ADAPTER_DIR/config.json" ]]; then
    # Full HF checkpoint (previous RL iter / merged weights), not a LoRA dir.
    RL_INIT_MODEL="$ADAPTER_DIR"
  else
    RL_INIT_MODEL="${MODEL_OVERRIDE:-${BASE_MODEL:-$MODEL}}"
  fi
fi
echo "RL init model: $RL_INIT_MODEL"
echo "RL output    : $RL_OUTPUT_DIR"
mkdir -p "$RL_OUTPUT_DIR" "$LOG_ROOT"

# ── Hyperparams ─────────────────────────────────────────────────────────────
RL_GPUS="${RL_GPUS:-${SFT_GPUS:-${GPU_POOL:-0,1,2,3,4,5,6,7}}}"
NPROC="$(awk -F',' '{print NF}' <<<"$RL_GPUS")"
if (( NPROC >= 2 )); then
  # A 50/50 split is not always right. ZeRO-3 shards params+grads+optimizer over
  # the learners, so a 9B needs ~27GB/GPU on 4 learners and OOMs on 2 — while the
  # vLLM engines only hold weights + KV. Override per model size:
  #   RL_N_LEARNERS=6 RL_N_VLLM=2 ...
  N_LEARNERS="${RL_N_LEARNERS:-$(( NPROC / 2 ))}"
  N_VLLM="${RL_N_VLLM:-$(( NPROC - N_LEARNERS ))}"
  if (( N_LEARNERS + N_VLLM > NPROC )); then
    echo "ERROR: RL_N_LEARNERS($N_LEARNERS) + RL_N_VLLM($N_VLLM) > GPUs($NPROC)" >&2
    exit 2
  fi
  SINGLE_GPU_ARGS=()
else
  N_LEARNERS=1
  N_VLLM=1
  SINGLE_GPU_ARGS=(--single_gpu_mode --vllm_sync_backend gloo --vllm_gpu_memory_utilization 0.35 --vllm_enforce_eager)
fi

RL_SEQUENCE_PARALLEL="${RL_SEQUENCE_PARALLEL:-1}"
# open_instruct defaults gather_whole_model=True (grpo_utils.py:243), which makes
# broadcast_to_vllm() call deepspeed.zero.GatheredParameters(model.parameters())
# — the ENTIRE model materialised on every rank at once (vllm_utils.py:1642).
# On 40GB cards that blew up in _allgather_params_coalesced with 33GB already
# allocated per learner, before a single rollout ran. With false, vllm_utils.py:1644
# gathers one parameter at a time inside its own GatheredParameters context, so
# the peak is one tensor instead of the whole model. The official 27B run sets the
# same flag for the same reason. Only FSDP1 requires True (vllm_utils.py:1680).
RL_GATHER_WHOLE_MODEL="${RL_GATHER_WHOLE_MODEL:-false}"
# deepspeed_zpg maps to zero_hpz_partition_size (utils.py:1448) — ZeRO++ hpZ, which
# keeps a SECONDARY bf16 replica of the weights inside each hpZ group to avoid
# cross-node all-gathers. That trade is for multi-node bandwidth; on one node it
# only costs memory, and the default 8 does not even divide our 6 learners. The
# official 27B run sets 1 for the same reason.
RL_DEEPSPEED_ZPG="${RL_DEEPSPEED_ZPG:-1}"
RL_EPISODES="${RL_EPISODES:-512}"
RL_SAMPLES_PER_PROMPT="${RL_SAMPLES_PER_PROMPT:-8}"
RL_UNIQUE_PROMPTS="${RL_UNIQUE_PROMPTS:-4}"
RL_MAX_STEPS="${RL_MAX_STEPS:-40}"
RL_PER_TURN_MAX_TOKENS="${RL_PER_TURN_MAX_TOKENS:-4096}"
RL_RESPONSE_LENGTH="${RL_RESPONSE_LENGTH:-16384}"
RL_LR="${RL_LR:-1e-6}"
# Derived below from the rollout group: see the note next to POOL_NEEDED.
RL_POOL_SIZE="${RL_POOL_SIZE:-}"
RL_EXP_NAME="${RL_EXP_NAME:-tmax-coevolve-rl}"
RL_ASYNC_STEPS="${RL_ASYNC_STEPS:-2}"
RL_DEEPSPEED_STAGE="${RL_DEEPSPEED_STAGE:-3}"
# 1 = drop zero-std groups (large runs); 0 for tiny smokes
RL_FILTER_ZERO_STD="${RL_FILTER_ZERO_STD:-1}"
RL_SYSTEM_PROMPT_FILE="${RL_SYSTEM_PROMPT_FILE:-$OPEN_INSTRUCT_ROOT/scripts/train/debug/envs/swerl_vanillux_sandbox_system_prompt.txt}"
# The parser has to match the family's tool-call syntax. Qwen3.5/3.6 emit the XML
# form (scripts/tmax/RL/qwen35_9b.sh uses vllm_qwen3_xml); Qwen3 *-Instruct emits
# the Hermes form (scripts/train/debug/envs/swerl_sandbox_8gpu.sh pairs that model
# with vllm_hermes). Get it wrong and nothing errors: the harness simply parses no
# tool calls, every episode ends on its first turn with
# bash/avg_calls_per_rollout = 0, and every reward is 0.
RL_TOOL_PARSER="${RL_TOOL_PARSER:-vllm_qwen3_xml}"
_model_lc="$(tr '[:upper:]' '[:lower:]' <<<"${RL_INIT_MODEL:-}")"
if [[ "$RL_TOOL_PARSER" == "vllm_qwen3_xml" && "$_model_lc" == *instruct* \
      && "$_model_lc" != *qwen3.5* && "$_model_lc" != *qwen3.6* ]]; then
  echo "WARNING: ${RL_INIT_MODEL} with tool_parser_type=$RL_TOOL_PARSER."
  echo "         Qwen3 *-Instruct emits Hermes-style tool calls; the official debug"
  echo "         script pairs it with vllm_hermes. Symptom of a mismatch is"
  echo "         bash/avg_calls_per_rollout = 0. Set RL_TOOL_PARSER=vllm_hermes."
fi

export VLLM_ALLOW_INSECURE_SERIALIZATION="${VLLM_ALLOW_INSECURE_SERIALIZATION:-1}"
export VLLM_DISABLE_COMPILE_CACHE="${VLLM_DISABLE_COMPILE_CACHE:-1}"
export VLLM_USE_V1="${VLLM_USE_V1:-1}"
export SWERL_SANDBOX_TIMING_LOGS="${SWERL_SANDBOX_TIMING_LOGS:-1}"
export SWERL_RESET_FAILURE_ZERO_REWARD="${SWERL_RESET_FAILURE_ZERO_REWARD:-1}"
export SWERL_DOCKER_AUTO_REMOVE="${SWERL_DOCKER_AUTO_REMOVE:-1}"
export PYTORCH_ALLOC_CONF="${PYTORCH_ALLOC_CONF:-expandable_segments:True}"
export SWERL_CONTAINER_RUNTIME="${SWERL_CONTAINER_RUNTIME:-docker}"

# vLLM 0.19's sagemaker router imports model_hosting_container_standards, whose
# loader reads SAGEMAKER_MODEL_PATH (default "/opt/ml/model/") and probes
# <path>/model.py with pathlib.Path.is_file(). On this cluster /opt/ml/model is
# not readable by us, and Path.is_file() does NOT swallow EACCES (only ENOENT,
# ENOTDIR, EBADF, ELOOP), so the PermissionError escapes and takes the vLLM
# EngineCore process down *after* it has finished loading weights and capturing
# CUDA graphs — 3.5 minutes of work per engine, then "Engine core proc EngineCore
# died unexpectedly". Pointing it at a readable empty dir makes the same probe
# return False and the loader a no-op. scripts/serve_pool.sh already does exactly
# this for the serving path; the RL path needs it too.
#
# Reaching the Ray actors is not luck: grpo_fast.py:3248 ships the whole driver
# environment as the Ray JOB runtime_env
#   "env_vars": {k: v for k, v in os.environ.items() if k not in EXCLUDED_ENV_VARS}
# and EXCLUDED_ENV_VARS (grpo_fast.py:181) is only {CUDA_VISIBLE_DEVICES,
# ROCR_VISIBLE_DEVICES}. The per-actor runtime_env at vllm_utils.py:1513 sets three
# other keys, and ray merges env_vars per key rather than replacing the map, so
# this survives into every LLMRayActor and into the engine-core child it forks.
export SAGEMAKER_MODEL_PATH="${SAGEMAKER_MODEL_PATH:-/tmp/${USER:-user}_sm_model_empty}"
mkdir -p "$SAGEMAKER_MODEL_PATH"
# The guard below only checks model.py; CUSTOM_SCRIPT_FILENAME would rename what
# the loader probes (sagemaker_loader.py:50-52) and slip past it.
unset CUSTOM_SCRIPT_FILENAME || true
if [[ -e "$SAGEMAKER_MODEL_PATH/model.py" ]]; then
  echo "ERROR: $SAGEMAKER_MODEL_PATH/model.py exists — the sagemaker loader would import it" >&2
  exit 2
fi

# Two different temp dirs, because they have incompatible requirements.
#
# General scratch (compile caches, HF temp) just needs space: /tmp lives on the
# 34G root volume that the container image store already shares, and filling it
# takes down docker — which IS the sandbox the rollouts run in. Lustre is fine.
RL_TMPDIR="${RL_TMPDIR:-$ROOT/.rl_tmp}"
mkdir -p "$RL_TMPDIR"
export TMPDIR="$RL_TMPDIR"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-$RL_TMPDIR/triton}"
export TORCHINDUCTOR_CACHE_DIR="${TORCHINDUCTOR_CACHE_DIR:-$RL_TMPDIR/inductor}"
#
# Ray's session dir must be on a LOCAL filesystem: it holds the raylet/plasma
# unix domain sockets, and Lustre does not support those — pointing RAY_TMPDIR at
# /fsx makes the head node fail to start. Prefer a writable local scratch, then
# /dev/shm (tmpfs, sockets work, RAM-backed), then /tmp as the last resort.
if [[ -z "${RL_RAY_TMPDIR:-}" ]]; then
  for cand in "/opt/dlami/nvme/${USER:-user}/ray" "/opt/sagemaker/${USER:-user}/ray"               "/dev/shm/${USER:-user}/ray" "/tmp/${USER:-user}/ray"; do
    if mkdir -p "$cand" 2>/dev/null && [[ -w "$cand" ]]; then RL_RAY_TMPDIR="$cand"; break; fi
  done
fi
RL_RAY_TMPDIR="${RL_RAY_TMPDIR:?could not find a writable local dir for RAY_TMPDIR}"
export RAY_TMPDIR="$RL_RAY_TMPDIR"
_ray_avail="$(df -BG --output=avail "$RL_RAY_TMPDIR" 2>/dev/null | tail -1 | tr -dc '0-9')"
echo "RL scratch   : $RL_TMPDIR"
echo "RAY_TMPDIR   : $RL_RAY_TMPDIR (${_ray_avail:-?}G free, must be local for ray sockets)"
if [[ -n "${_ray_avail:-}" ]] && (( _ray_avail < 20 )); then
  echo "  WARN: <20G there. Ray spills objects into this dir; if it shares the volume"
  echo "        with the docker image store, a spill can wedge the sandbox."
fi
export PYTHONPATH="${OPEN_INSTRUCT_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

# Absolute paths — grpo_fast runs with cwd=open-instruct.
TRAIN_MIXER="$(cd "$(dirname "$TRAIN_MIXER")" && pwd)/$(basename "$TRAIN_MIXER")"
TASK_DATA_DIR="$(cd "$TASK_DATA_DIR" && pwd)"
if [[ -d "$RL_INIT_MODEL" ]]; then
  RL_INIT_MODEL="$(cd "$RL_INIT_MODEL" && pwd)"
  # Cheap (json + safetensors headers). Catches a bare Qwen3.5 text tower that
  # vLLM cannot load, before we spend minutes on ray / sandbox startup.
  _audit="$ROOT/scripts/tmax/check_rl_ckpt.py"
  if [[ -f "$_audit" ]]; then
    python3 "$_audit" "$RL_INIT_MODEL" \
      || { echo "ERROR: RL init checkpoint failed the VLM/tokenizer audit: $RL_INIT_MODEL" >&2; exit 2; }
  fi
fi
RL_OUTPUT_DIR="$(mkdir -p "$RL_OUTPUT_DIR" && cd "$RL_OUTPUT_DIR" && pwd)"

TOOL_CONFIGS="$(cat <<EOF
{"task_data_dir": "$TASK_DATA_DIR", "test_timeout": 120, "image": "python:3.12-slim"}
EOF
)"

# grpo_fast.validate_configs (open_instruct/grpo_fast.py:1786) enforces:
#   1. samples_per_prompt * unique_prompts >= world_size / sequence_parallel_size
#      — every learner rank needs a batch, so the rollout group cannot be smaller
#        than the learner count. Violating it is a hard AssertionError before any
#        rollout runs (this is what `1 x 4` with 6 learners hit).
#   2. unique_prompts >= vllm_num_engines, else engines interleave batches (warn).
# Both are pure arithmetic over knobs we already set, so derive rather than guess.
MIN_SEQS=$(( N_LEARNERS / RL_SEQUENCE_PARALLEL ))
(( MIN_SEQS < 1 )) && MIN_SEQS=1
NEED_PROMPTS=$(( (MIN_SEQS + RL_SAMPLES_PER_PROMPT - 1) / RL_SAMPLES_PER_PROMPT ))
(( NEED_PROMPTS < N_VLLM )) && NEED_PROMPTS=$N_VLLM
if (( RL_UNIQUE_PROMPTS < NEED_PROMPTS )); then
  echo "NOTE: raising RL_UNIQUE_PROMPTS ${RL_UNIQUE_PROMPTS} -> ${NEED_PROMPTS}"        "(learners=$N_LEARNERS sp=$RL_SEQUENCE_PARALLEL samples=$RL_SAMPLES_PER_PROMPT engines=$N_VLLM)"
  RL_UNIQUE_PROMPTS=$NEED_PROMPTS
fi
GROUP=$(( RL_UNIQUE_PROMPTS * RL_SAMPLES_PER_PROMPT ))
if (( GROUP < MIN_SEQS )); then
  echo "ERROR: rollout group $GROUP < world/sp $MIN_SEQS — raise RL_SAMPLES_PER_PROMPT" >&2
  echo "       or RL_UNIQUE_PROMPTS, or lower RL_N_LEARNERS." >&2
  exit 2
fi
# grpo_fast.py:3280 additionally requires the dataset to hold
#   max(async_steps, 1) * num_unique_prompts_rollout
# distinct prompts, so the prefill pipeline always has work queued. Derive it here
# instead of discovering it after the GPUs are already reserved: clamp async_steps
# to what the task set can feed, and only fail if even 1 cannot be satisfied.
POOL_NEEDED=$(( (RL_ASYNC_STEPS < 1 ? 1 : RL_ASYNC_STEPS) * RL_UNIQUE_PROMPTS ))
if (( POOL_NEEDED > n_tasks )); then
  max_async=$(( n_tasks / RL_UNIQUE_PROMPTS ))
  if (( max_async >= 1 )); then
    echo "NOTE: lowering RL_ASYNC_STEPS ${RL_ASYNC_STEPS} -> ${max_async}"          "(dataset has $n_tasks task(s), needs async_steps x prompts <= $n_tasks)"
    RL_ASYNC_STEPS=$max_async
    POOL_NEEDED=$(( RL_ASYNC_STEPS * RL_UNIQUE_PROMPTS ))
  else
    echo "ERROR: dataset has $n_tasks task(s) but needs at least $RL_UNIQUE_PROMPTS" >&2
    echo "       (1 async step x $RL_UNIQUE_PROMPTS unique prompts). Rebuild the task" >&2
    echo "       set with RL_N_TASKS >= $RL_UNIQUE_PROMPTS, or lower RL_UNIQUE_PROMPTS." >&2
    exit 2
  fi
fi

# One optimizer step consumes a whole group; fewer episodes than that trains on
# nothing and never writes a checkpoint.
if (( RL_EPISODES < GROUP )); then
  echo "NOTE: raising RL_EPISODES ${RL_EPISODES} -> ${GROUP} (one step = ${GROUP} episodes)"
  RL_EPISODES=$GROUP
fi

# Each pool slot is a live sandbox actor holding its own container, and Tmax tasks
# install packages inside them (apt-get, pip), so the pool size is the main driver
# of image-store usage. The stock 64 with a group of 8 meant 56 idle containers:
# it filled a node's 34GB /var/lib/containerd and the node was drained. Size the
# pool to the concurrency the rollout shape actually needs.
if [[ -z "$RL_POOL_SIZE" ]]; then
  RL_POOL_SIZE=$(( GROUP + N_VLLM ))
  (( RL_POOL_SIZE < 4 )) && RL_POOL_SIZE=4
fi

PACK_LENGTH=$(( RL_RESPONSE_LENGTH + 2048 ))
FILTER_ARG=(--filter_zero_std_samples true)
[[ "$RL_FILTER_ZERO_STD" == "0" ]] && FILTER_ARG=(--filter_zero_std_samples false)

SYSTEM_PROMPT_ARGS=()
if [[ -n "$RL_SYSTEM_PROMPT_FILE" && -f "$RL_SYSTEM_PROMPT_FILE" ]]; then
  SYSTEM_PROMPT_ARGS=(--system_prompt_override_file "$RL_SYSTEM_PROMPT_FILE")
fi

# Official Qwen3.5 RL (tmax/training/open-instruct/scripts/tmax/RL/qwen35_9b.sh)
# always passes --vllm_gdn_prefill_backend triton so vLLM skips FlashInfer GDN
# prefill JIT (the 0.24 log even tells you to set this). The msgspec
# MambaAttentionBackendEnum crash is patched in open_instruct/vllm_utils.py.
GDN_ARGS=()
RL_GDN_PREFILL_BACKEND="${RL_GDN_PREFILL_BACKEND:-triton}"
if [[ -n "$RL_GDN_PREFILL_BACKEND" && "$RL_GDN_PREFILL_BACKEND" != "0" ]]; then
  GDN_ARGS=(--vllm_gdn_prefill_backend "$RL_GDN_PREFILL_BACKEND")
fi

# Official also sets --lm_head_fp32 true for Qwen3.5 (bf16 logits round).
# --use_liger_grpo_loss is decided after RL_PY is known (needs liger_kernel).
LM_HEAD_ARGS=()
if [[ "${RL_LM_HEAD_FP32:-1}" == "1" ]]; then
  LM_HEAD_ARGS=(--lm_head_fp32 true)
fi
LIGER_ARGS=()

echo "===== RL plan ====="
echo "  mixer       : $TRAIN_MIXER ($n_tasks tasks; holdout excluded)"
echo "  task_data   : $TASK_DATA_DIR"
echo "  init model  : $RL_INIT_MODEL"
echo "  gpus        : $RL_GPUS (learners=$N_LEARNERS vllm=$N_VLLM)"
echo "  episodes    : $RL_EPISODES"
echo "  group       : prompts=$RL_UNIQUE_PROMPTS x samples=$RL_SAMPLES_PER_PROMPT = $GROUP seq/step"      "(needs >= world/sp = $MIN_SEQS)"
echo "  steps       : ~$(( RL_EPISODES / GROUP )) (episodes=$RL_EPISODES)"
echo "  prompt pool : need async($RL_ASYNC_STEPS) x prompts($RL_UNIQUE_PROMPTS) = $POOL_NEEDED <= $n_tasks tasks"
echo "  max_steps   : $RL_MAX_STEPS  per_turn=$RL_PER_TURN_MAX_TOKENS  resp=$RL_RESPONSE_LENGTH"
echo "  filter0std  : $RL_FILTER_ZERO_STD  tool_parser=$RL_TOOL_PARSER"
echo "  sandbox pool: $RL_POOL_SIZE concurrent container(s)"
echo "  lr          : $RL_LR  loss=dppo  gather_whole_model=$RL_GATHER_WHOLE_MODEL zpg=$RL_DEEPSPEED_ZPG"
echo "  vllm extras : gdn=${RL_GDN_PREFILL_BACKEND:-off} lm_head_fp32=${RL_LM_HEAD_FP32:-1}"

# The coevolve loop calls this script without RL_PYTHON, and the old fallback
# chain landed on SFT_PYTHON — the conda env that has trl but NO ray/deepspeed/
# openenv, so ENABLE_RL=1 died at the dependency check. Prefer the env built from
# open-instruct's own lockfile whenever it exists (scripts/tmax/setup_rl_env.sh).
_OI_VENV_PY="$OPEN_INSTRUCT_ROOT/.venv/bin/python"
if [[ -z "${RL_PYTHON:-}" && -x "$_OI_VENV_PY" ]]; then
  RL_PYTHON="$_OI_VENV_PY"
fi
RL_PY="${RL_PYTHON:-${SFT_PYTHON:-${PYTHON_BIN:-$(python_bin)}}}"

# Official Qwen3.5 recipe also passes --use_liger_grpo_loss. serving ~/.venv
# does not ship liger_kernel, so only enable it when the env actually has it.
if [[ "${RL_USE_LIGER:-0}" == "1" ]]; then
  if "$RL_PY" -c "import importlib.util as u; raise SystemExit(0 if u.find_spec('liger_kernel') else 1)" 2>/dev/null; then
    LIGER_ARGS=(--use_liger_grpo_loss --liger_grpo_loss_chunk_size "${RL_LIGER_CHUNK:-8}")
  else
    echo "NOTE: RL_USE_LIGER=1 but liger_kernel is not installed in $RL_PY; skipping"
  fi
fi
echo "  liger       : ${LIGER_ARGS[*]:-off}"

# Flashinfer JIT-compiles the top-k/top-p sampler the first time vLLM
# profile_run's it (v1/sample/ops/topk_topp_sampler.py → flashinfer.jit).
# That subprocess is a raw `ninja` on PATH, not a Python import. Official
# open-instruct uv.lock ships ninja; the serving ~/.venv fallback does not,
# which killed iter1 RL with FileNotFoundError: 'ninja' after 12 min of
# engine startup. Put this interpreter's bin first so Ray EngineCore
# children (they inherit env_vars from the driver) can see it too.
_rl_bin="$(cd "$(dirname "$RL_PY")" && pwd)"
export PATH="${_rl_bin}${CUDA_HOME:+:$CUDA_HOME/bin}:$PATH"
if [[ -z "${CUDA_HOME:-}" ]]; then
  for _ch in /usr/local/cuda /usr/local/cuda-12.9 /usr/local/cuda-12.8 /usr/local/cuda-12.4; do
    if [[ -x "$_ch/bin/nvcc" ]]; then
      export CUDA_HOME="$_ch"
      export PATH="$CUDA_HOME/bin:$PATH"
      break
    fi
  done
fi
export FLASHINFER_WORKSPACE_BASE="${FLASHINFER_WORKSPACE_BASE:-$RL_TMPDIR/flashinfer}"
mkdir -p "$FLASHINFER_WORKSPACE_BASE"

# Optional auto-install of critical missing packages (smoke / first coevolve RL).
if [[ "${INSTALL_RL_DEPS:-0}" == "1" ]]; then
  "$RL_PY" - <<'PY'
import importlib.util as u, shutil, subprocess, sys
need=[]
for mod, pip in [("ray","ray[default]>=2.9"),("deepspeed","deepspeed>=0.14"),
                 ("openenv","openenv-core>=0.2.1"),("docker","docker>=7.0"),
                 ("ninja","ninja")]:
    if u.find_spec(mod) is None:
        need.append(pip)
if shutil.which("ninja") is None and "ninja" not in need:
    need.append("ninja")
if need:
    print("Installing RL deps:", need)
    subprocess.check_call([sys.executable,"-m","pip","install","-q",*need])
PY
  hash -r 2>/dev/null || true
fi

if ! command -v ninja >/dev/null 2>&1; then
  echo "ERROR: ninja is not on PATH (flashinfer sampler JIT needs the binary)." >&2
  echo "       RL_PYTHON=$RL_PY" >&2
  echo "       Install into that env:  $RL_PY -m pip install ninja" >&2
  echo "       Or skip the JIT path:   export VLLM_USE_FLASHINFER_SAMPLER=0" >&2
  exit 2
fi
echo "ninja        : $(command -v ninja) ($(ninja --version 2>/dev/null || echo '?'))"
echo "CUDA_HOME    : ${CUDA_HOME:-unset}  nvcc=$(command -v nvcc 2>/dev/null || echo missing)"
echo "FLASHINFER_WORKSPACE_BASE: $FLASHINFER_WORKSPACE_BASE"

# A real import of vllm+torch costs ~3.5 minutes cold on Lustre, and inside a GPU
# job every second of it is time the cards are reserved and doing nothing. The
# strict version belongs in the CPU-side preparation step
# (scripts/tmax/prep_rl_smoke.sh); here find_spec catches a missing or half-built
# environment without importing anything. RL_STRICT_DEP_CHECK=1 forces the full
# import when this script is run standalone.
"$RL_PY" - <<PY
import importlib, importlib.util
dry = 1 if ("${DRY_RUN:-0}" == "1" or "${RL_STRICT_DEP_CHECK:-0}" != "1") else 0
missing=[]
for m in ("ray","deepspeed","datasets","transformers","torch","vllm"):
    try:
        if dry:
            if importlib.util.find_spec(m) is None:
                missing.append(m)
        else:
            importlib.import_module(m)
    except Exception:
        missing.append(m)
try:
    if dry:
        if importlib.util.find_spec("openenv") is None:
            missing.append("openenv")
    else:
        import openenv.core  # noqa: F401
except Exception:
    missing.append("openenv.core")
if missing:
    raise SystemExit(
        "ERROR: RL python missing modules: " + ", ".join(missing) +
        "\n       Install into RL_PYTHON (ray, deepspeed, openenv-core, vllm)."
    )
print("RL deps OK" + (" (find_spec; strict check runs in prep)" if dry else " (full import)"))
PY

mkdir -p "$LOG_ROOT"
cd "$OPEN_INSTRUCT_ROOT"

# Assemble the command as an array so DRY_RUN can print exactly what would run.
# Everything above (rollout shape, tmpdirs, image gate, init model) has already
# been derived, so a dry run reflects the real invocation rather than a guess.
CMD=(
  "$RL_PY" open_instruct/grpo_fast.py \
  --dataset_mixer_list "$TRAIN_MIXER" 1.0 \
  --dataset_mixer_list_splits train \
  --max_prompt_token_length 2048 \
  --per_turn_max_tokens "$RL_PER_TURN_MAX_TOKENS" \
  --response_length "$RL_RESPONSE_LENGTH" \
  --pack_length "$PACK_LENGTH" \
  --per_device_train_batch_size 1 \
  --num_unique_prompts_rollout "$RL_UNIQUE_PROMPTS" \
  --num_samples_per_prompt_rollout "$RL_SAMPLES_PER_PROMPT" \
  --async_steps "$RL_ASYNC_STEPS" \
  --model_name_or_path "$RL_INIT_MODEL" \
  --temperature 1.0 \
  --learning_rate "$RL_LR" \
  --total_episodes "$RL_EPISODES" \
  --lr_scheduler_type constant \
  --deepspeed_stage "$RL_DEEPSPEED_STAGE" \
  --num_epochs 1 \
  --num_learners_per_node "$N_LEARNERS" \
  --sequence_parallel_size "$RL_SEQUENCE_PARALLEL" \
  --vllm_num_engines "$N_VLLM" \
  --vllm_tensor_parallel_size 1 \
  "${SINGLE_GPU_ARGS[@]}" \
  --beta 0.0 \
  --use_vllm_logprobs true \
  --gather_whole_model "$RL_GATHER_WHOLE_MODEL" \
  --deepspeed_zpg "$RL_DEEPSPEED_ZPG" \
  --truncated_importance_sampling_ratio_cap 0.0 \
  --seed 42 \
  --gradient_checkpointing \
  --vllm_enable_prefix_caching \
  "${GDN_ARGS[@]}" \
  "${LM_HEAD_ARGS[@]}" \
  "${LIGER_ARGS[@]}" \
  --inflight_updates true \
  --push_to_hub false \
  --tools swerl_vanillux_sandbox \
  --tool_configs "$TOOL_CONFIGS" \
  --pool_size "$RL_POOL_SIZE" \
  --max_steps "$RL_MAX_STEPS" \
  --verification_reward 1.0 \
  --tool_parser_type "$RL_TOOL_PARSER" \
  "${FILTER_ARG[@]}" \
  "${SYSTEM_PROMPT_ARGS[@]}" \
  --backend_timeout 1200 \
  --advantage_normalization_type centered \
  --loss_fn dppo \
  --dppo_divergence_type tv \
  --dppo_divergence_threshold 0.1 \
  --output_dir "$RL_OUTPUT_DIR" \
  --exp_name "$RL_EXP_NAME" \
  --save_freq "${RL_SAVE_FREQ:-50}" \
  --checkpoint_state_freq "${RL_CKPT_FREQ:-25}"
)

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo
  echo "===== DRY_RUN: command that would run (cwd=$PWD) ====="
  echo "CUDA_VISIBLE_DEVICES=$RL_GPUS \\"
  printf '  %q \\\n' "${CMD[@]}" | sed 's/\\$/\\/'
  echo
  echo "SAGEMAKER_MODEL_PATH=$SAGEMAKER_MODEL_PATH  RAY_TMPDIR=$RAY_TMPDIR  TMPDIR=$TMPDIR"
  echo "DRY_RUN=1 — nothing launched."
  exit 0
fi

# Make failure terminate promptly. Observed once: grpo_fast raised inside a Ray
# task and the driver died, but surviving actors kept the stdout pipe open, so
# `tee` never saw EOF and this script never returned. Two guards: a hard wall
# clock on the trainer, and an EXIT trap that reaps Ray and sandbox containers
# on every exit path so the job releases its resources immediately.
rl_cleanup() {
  local rc=$?
  # `set -e` is in force inside an EXIT trap too, and a command failing there
  # replaces the script's status. That reported two clean runs as slurm FAILED:
  # this function removed the docker proxy first, which invalidated DOCKER_HOST,
  # so the following `docker ps` could not connect, its assignment failed, and the
  # trap exited 1 without ever reaching `return $rc`. Cleanup must never decide
  # the verdict, so disable errexit and take the proxy down last.
  set +e
  unset DOCKER_HOST   # the setgid CLI reaches the socket without the proxy
  local stale
  stale="$(docker ps -q --filter "ancestor=hx-tmax-base:1" 2>/dev/null; \
           docker ps -aq --filter "name=^/swerl" 2>/dev/null)"
  [[ -n "$stale" ]] && docker rm -f $stale >/dev/null 2>&1
  [[ -n "${DOCKER_PROXY_NAME:-}" ]] && docker rm -f "$DOCKER_PROXY_NAME" >/dev/null 2>&1
  local ray_bin="$(dirname "$RL_PY")/ray"
  [[ -x "$ray_bin" ]] && "$ray_bin" stop --force >/dev/null 2>&1
  return $rc
}
trap rl_cleanup EXIT

# The sandbox backend talks to docker through the python SDK
# (backends.py:205 docker_sdk.from_env). On this cluster /usr/bin/docker is a
# setgid-docker binary, so the CLI reaches /var/run/docker.sock while a plain
# process — every Ray sandbox actor — gets EACCES and every task reset fails
# ("Error while fetching server API version ... PermissionError(13)"), which
# silently turns every episode into a zero-reward rollout.
# Publishing the same socket on loopback via a container started by the setgid
# CLI gives the SDK an endpoint it is allowed to use. Skipped entirely when the
# SDK can already reach docker (e.g. a user in the docker group).
if [[ "${RL_DOCKER_PROXY:-1}" == "1" && -z "${DOCKER_HOST:-}" ]]; then
  if "$RL_PY" -c "import docker; docker.from_env(timeout=10).version()" >/dev/null 2>&1; then
    echo "docker: SDK reaches the daemon directly"
  else
    DOCKER_PROXY_PORT="${RL_DOCKER_PROXY_PORT:-$(( 24000 + (${SLURM_JOB_ID:-$$} % 1000) ))}"
    DOCKER_PROXY_NAME="hx-dockproxy-${SLURM_JOB_ID:-$$}"
    docker rm -f "$DOCKER_PROXY_NAME" >/dev/null 2>&1 || true
    echo "docker: SDK cannot reach the socket; publishing it on 127.0.0.1:${DOCKER_PROXY_PORT}"
    docker run -d --name "$DOCKER_PROXY_NAME" --restart no       -p "127.0.0.1:${DOCKER_PROXY_PORT}:2375"       -v /var/run/docker.sock:/var/run/docker.sock       "${RL_DOCKER_PROXY_IMAGE:-alpine/socat}"       tcp-listen:2375,fork,reuseaddr unix-connect:/var/run/docker.sock >/dev/null
    export DOCKER_HOST="tcp://127.0.0.1:${DOCKER_PROXY_PORT}"
    for _ in $(seq 1 30); do
      "$RL_PY" -c "import docker; docker.from_env(timeout=5).version()" >/dev/null 2>&1 && break
      sleep 1
    done
    "$RL_PY" -c "import docker; print('docker: SDK ok via', __import__('os').environ['DOCKER_HOST'], '->', docker.from_env(timeout=10).version()['Version'])" || {
      echo "ERROR: docker SDK still cannot reach the daemon; sandbox resets would all fail" >&2
      exit 2
    }
  fi
fi

# pipefail is set so that grpo_fast's exit status survives the pipe into tee;
# without it a crashed trainer looks like success to every caller of this script.
set -o pipefail
RL_WALL_TIMEOUT="${RL_WALL_TIMEOUT:-5400}"   # seconds; 0 disables
timeout_cmd=()
if [[ "$RL_WALL_TIMEOUT" != "0" ]]; then
  timeout_cmd=(timeout --signal=TERM --kill-after=120 "$RL_WALL_TIMEOUT")
  echo "  wall cap    : ${RL_WALL_TIMEOUT}s on the trainer (RL_WALL_TIMEOUT=0 to disable)"
fi
# Reclaim image-store space before starting. Every rollout container installs
# packages into its writable layer, and a node accumulates task images from older
# datasets across jobs, so /var/lib/containerd fills up and every task reset dies
# with "no space left on device" — which looks exactly like a sandbox bug. Only
# our own leftovers are touched: stopped containers, dangling layers, build cache,
# and tmax-eval images that this dataset does not use.
if [[ "${RL_RECLAIM_DISK:-1}" == "1" ]]; then
  export DATA_ROOT
  keep_file="$(mktemp)"
  "$(python_bin)" - >"$keep_file" <<'PY'
import json, os
p = os.path.join(os.environ["DATA_ROOT"], "train.jsonl")
print("\n".join(sorted({json.loads(l)["env_config"]["image"] for l in open(p) if l.strip()})))
PY
  before="$(df -BG --output=avail /var/lib/containerd 2>/dev/null | tail -1 | tr -dc '0-9')"
  docker container prune -f >/dev/null 2>&1 || true
  while read -r tag; do
    [[ -n "$tag" ]] || continue
    grep -qxF "$tag" "$keep_file" || docker image rm -f "$tag" >/dev/null 2>&1
  done < <(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep '^tmax-eval:' || true)
  docker image prune -f >/dev/null 2>&1 || true
  docker builder prune -f >/dev/null 2>&1 || true
  rm -f "$keep_file"
  after="$(df -BG --output=avail /var/lib/containerd 2>/dev/null | tail -1 | tr -dc '0-9')"
  echo "image store: ${before:-?}G -> ${after:-?}G free after reclaiming unused task images"
  if [[ -n "${after:-}" ]] && (( after < ${RL_MIN_STORE_GB:-15} )); then
    echo "ERROR: only ${after}G free on the image store; rollout containers need room" >&2
    echo "       to install packages. Free space on this node or pick another." >&2
    exit 2
  fi
fi

# Capture the trainer's own status rather than letting `set -e` turn any nonzero
# into an unexplained script failure. A run that trains, saves and shuts down
# cleanly but returns 1 during interpreter teardown is a very different situation
# from a crash, and the difference has to be visible.
set +e
CUDA_VISIBLE_DEVICES="$RL_GPUS" "${timeout_cmd[@]}" "${CMD[@]}" 2>&1 | tee "$LOG_ROOT/train_rl_grpo.log"
rc_train=${PIPESTATUS[0]}
set -e
echo "trainer exit status: $rc_train"

if [[ -f "$LOG_ROOT/train_rl_grpo.log" ]]; then
  echo "===== RL metrics (from train log) ====="
  grep -E 'training_step|objective|approx_kl|non_submitting|scores|loss |reward' \
    "$LOG_ROOT/train_rl_grpo.log" | tail -60 || true
fi

# save_final_model writes into <output_dir>/<exp_name>__<seed>__<timestamp>/, and
# save_freq writes <...>_checkpoints/step_N/. Look for the weights themselves
# instead of assuming a layout.
final_ckpt="$(find "$RL_OUTPUT_DIR" -maxdepth 2 -name config.json -not -path "*_checkpoints/*" 2>/dev/null | head -1)"
step_ckpts="$(find "$RL_OUTPUT_DIR" -maxdepth 3 -type d -name "step_*" 2>/dev/null | sort | tr '\n' ' ')"
if [[ -n "$final_ckpt" ]]; then
  echo "RL final checkpoint: $(dirname "$final_ckpt")"
  dirname "$final_ckpt" >"$RL_OUTPUT_DIR/checkpoint.path"
else
  echo "WARNING: no final checkpoint under $RL_OUTPUT_DIR" >&2
fi
[[ -n "$step_ckpts" ]] && echo "step checkpoints: $step_ckpts"
echo "$RL_OUTPUT_DIR" >"$ROOT/outputs/rl/${RL_DATASET_NAME}.path" 2>/dev/null || true
exit "$rc_train"
