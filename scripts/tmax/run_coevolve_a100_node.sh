#!/usr/bin/env bash
# Run the Tmax coevolve loop on a node you already hold (interactive alloc or
# ssh'd into a reserved node) — no sbatch. 8x A100-40GB, Qwen3.5-9B base,
# GPT-5.5 meta-agent through the Salesforce gateway.
#
#   export SFG_API_KEY=<your key>            # or put it in .env
#   bash scripts/tmax/run_coevolve_a100_node.sh              # foreground (use tmux)
#   DETACH=1 bash scripts/tmax/run_coevolve_a100_node.sh     # setsid+nohup, survives logout
#   N_ITERS=1 EVOLVE_ROUNDS=2 MAX_SFT_RETRIES=0 bash scripts/tmax/run_coevolve_a100_node.sh   # smoke
#
# Everything here is a preflight + a tuned env; the loop itself is
# scripts/tmax/run_loop_tmax_coevolve.sh and is resumable — re-running this
# script after a crash continues from the last completed stage.

set -Eeuo pipefail
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

# ── model + meta-agent ──────────────────────────────────────────────────────
export MODEL_SIZE="${MODEL_SIZE:-9b}"                  # Qwen/Qwen3.5-9B
export META_MODEL="${META_MODEL:-openai/gpt-5.5}"
export PROVIDER_ID="${PROVIDER_ID:-openai}"
export GATEWAY_URL="${GATEWAY_URL:-https://gateway.salesforceresearch.ai}"
# evolve_tmax.sh turns GATEWAY_URL into OPENAI_API_BASE=<url>/openai/process/v1
# and litellm picks that up for openai/* models. The gateway also wants
# X-Api-Key / X-Model-Provider-Id, which run.py adds from SFG_API_KEY+PROVIDER_ID.
export SFG_API_KEY="${SFG_API_KEY:-${OPENAI_API_KEY:-}}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-$SFG_API_KEY}"
# Optional, only if your gateway accepts it for gpt-5.5: META_REASONING_EFFORT=high

# ── 8-GPU utilisation ───────────────────────────────────────────────────────
# One vLLM replica per GPU (serve_pool.sh), and enough concurrent docker+agent
# workers to keep every replica batching. A single rollout is latency-bound —
# ~1 request in flight per replica leaves the GPU mostly idle between steps — so
# oversubscribe ~2x. Raise/lower with TMAX_CONCURRENT if docker or disk struggles.
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export TMAX_CONCURRENT="${TMAX_CONCURRENT:-16}"
# The stock default was 2, which left 6 of 8 replicas idle for every 102-task
# holdout pass — and holdout is 1-4 of those passes per iteration.
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-16}"
export MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.88}"   # 40GB cards
export POOL_BASE_PORT="${POOL_BASE_PORT:-8300}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

# SHARED_BASE=1 rebases every task image on one shared python3/pip/pytest layer
# (~15G for 152 images instead of ~60G). Requires the base image to exist —
# scripts/tmax/prebuild_tmax_images.sh SHARED_BASE=1 builds it. Image tags are
# identical either way, so mixing prebuilt and loop-built images is fine.
export SHARED_BASE="${SHARED_BASE:-0}"
if [[ "$SHARED_BASE" == "1" ]]; then
  export TMAX_SHARED_BASE_TAG="${TMAX_SHARED_BASE_TAG:-hx-tmax-base:1}"
fi

# ── SFT sizing for 40GB ─────────────────────────────────────────────────────
export SFT_MAX_SEQ_LENGTH="${SFT_MAX_SEQ_LENGTH:-8192}"
export SFT_PER_DEVICE_BS="${SFT_PER_DEVICE_BS:-1}"
export SFT_GRAD_CHECKPOINT="${SFT_GRAD_CHECKPOINT:-true}"
export SFT_EFFECTIVE_BATCH="${SFT_EFFECTIVE_BATCH:-32}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
# The vLLM venv deliberately has no trl/peft (it exists to serve). Training runs
# in the conda env that does. Keeping them separate is why train_sft.sh takes an
# absolute interpreter path instead of resolving `python`.
_SFT_PY_DEFAULT=/fsx/home/jixuan.chen/miniconda3/envs/vllm-019-cu128-clean/bin/python
[[ -x "$_SFT_PY_DEFAULT" ]] || _SFT_PY_DEFAULT="$PYTHON_BIN"
export SFT_PYTHON="${SFT_PYTHON:-$_SFT_PY_DEFAULT}"
# flash-attn is not installed in that env; sdpa is the working attention on A100.
export SFT_ATTN_IMPLEMENTATION="${SFT_ATTN_IMPLEMENTATION:-sdpa}"

# ── loop shape ──────────────────────────────────────────────────────────────
export REPLICATE="${REPLICATE:-1}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}"
export MIN_TRAJS="${MIN_TRAJS:-20}"
export MAX_TRAJS="${MAX_TRAJS:-1000}"
export PER_TASK="${PER_TASK:-3}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"
export CORPUS_PREFER_CURRENT="${CORPUS_PREFER_CURRENT:-1}"
export ENABLE_RL="${ENABLE_RL:-0}"                     # 40GB: no trainer+vLLM co-residency

# ratchet: accept a tie when that run had no system errors
export ACCEPT_TIES="${ACCEPT_TIES:-1}"
export TIE_MAX_SYSTEM_ERRORS="${TIE_MAX_SYSTEM_ERRORS:-0}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"

# rotate the evolve set every iteration
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"
export ROTATE_FROM_ITER="${ROTATE_FROM_ITER:-2}"
export EVOLVE_SET_SIZE="${EVOLVE_SET_SIZE:-50}"
export MASTERY_MIN_SUCCESSES="${MASTERY_MIN_SUCCESSES:-1}"
export TAXONOMY_PARQUET="${TAXONOMY_PARQUET:-$ROOT/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet}"
_TAXONOMY_FALLBACK=/fsx/home/jixuan.chen/qwen35-tb2-fullstack/data/external/tmax-taxonomy/data/train-00000-of-00001.parquet
if [[ ! -f "$TAXONOMY_PARQUET" && -f "$_TAXONOMY_FALLBACK" ]]; then
  export TAXONOMY_PARQUET="$_TAXONOMY_FALLBACK"
fi

HOLDOUT_ENVS="${HOLDOUT_ENVS_JSONL:-$ROOT/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl}"
EVOLVE_ENVS="${EVOLVE_ENVS_JSONL:-$ROOT/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl}"

LOG_DIR="${LOG_DIR:-$ROOT/logs/coevolve_rep${REPLICATE}}"
mkdir -p "$LOG_DIR" "$ROOT/outputs/tmax_coevolve"
RUN_LOG="$LOG_DIR/loop_$(date +%Y%m%d-%H%M%S).log"

fail() { echo "ERROR: $*" >&2; exit 2; }

echo "===== preflight ====="
echo "node    : $(hostname)"
echo "date    : $(date -u '+%F %T UTC')"

# 1. GPUs
n_gpu="$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l)"
n_pool="$(awk -F',' '{print NF}' <<<"$GPU_POOL")"
nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv,noheader || fail "nvidia-smi failed"
(( n_gpu >= n_pool )) || fail "GPU_POOL wants $n_pool GPU(s), node exposes $n_gpu"
busy="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | awk '$1>2000{n++} END{print n+0}')"
(( busy == 0 )) || echo "WARN: $busy GPU(s) already hold >2GB — another job may be resident"

# 2. docker: daemon, the filesystem the IMAGE STORE really lives on, and a real
#    one-layer build. Checking `docker info`'s DockerRootDir is not enough — with
#    the containerd image store the layers land in /var/lib/containerd, which on
#    these nodes is a separate, much smaller volume.
# With the 152 images already prebuilt, the loop itself only builds the 12-15
# tasks each rotation draws in, so it needs far less headroom than the prebuild.
if [[ "$SHARED_BASE" == "1" ]]; then
  : "${MIN_IMAGE_GB:=20}"
else
  : "${MIN_IMAGE_GB:=40}"
fi
MIN_IMAGE_GB="$MIN_IMAGE_GB" bash "$ROOT/scripts/tmax/docker_preflight.sh" || fail \
  "docker cannot build task images on this node (diagnosis above).
       If the image store is simply small, prebuild with a shared base layer:
         SHARED_BASE=1 JOBS=12 bash scripts/tmax/prebuild_tmax_images.sh
       then re-run this with SHARED_BASE=1."

# 3. interpreters + training deps
[[ -x "$PYTHON_BIN" ]] || fail "PYTHON_BIN not executable: $PYTHON_BIN"
[[ -x "$SFT_PYTHON" ]] || fail "SFT_PYTHON not executable: $SFT_PYTHON"
echo "serve   : $PYTHON_BIN"
echo "train   : $SFT_PYTHON"
missing="$("$SFT_PYTHON" - <<'PY'
import importlib.util
print(" ".join(m for m in ("trl", "peft", "datasets", "transformers", "torch")
                if not importlib.util.find_spec(m)))
PY
)"
[[ -z "$missing" ]] || fail "SFT deps missing from $SFT_PYTHON: $missing
       point SFT_PYTHON at an env that has them (trl/peft/datasets/transformers/torch)"
if [[ "$SFT_ATTN_IMPLEMENTATION" == "flash_attention_2" ]] &&    ! "$SFT_PYTHON" -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('flash_attn') else 1)"; then
  echo "WARN: flash_attn absent from $SFT_PYTHON — falling back to SFT_ATTN_IMPLEMENTATION=sdpa"
  export SFT_ATTN_IMPLEMENTATION=sdpa
fi
# pandas is what reads the taxonomy parquet for the rotation stage.
"$PYTHON_BIN" -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('pandas') else 1)"   || fail "pandas missing from $PYTHON_BIN — the evolve-set rotation cannot read the taxonomy parquet"

# 4. task sets + env jsonls
[[ -f "$HOLDOUT_ENVS" ]] || fail "missing holdout envs: $HOLDOUT_ENVS
       build it: IDS_JSON=recipe/tb2_evolver/tasks_tmax_only200.json \\
                 OUT_DIR=recipe/tb2_sft/data/qwen35_9b_tmax_only200 \\
                 bash scripts/tmax/build_tmax_envs_from_taxonomy.sh"
[[ -f "$EVOLVE_ENVS" ]] || fail "missing evolve-50 envs: $EVOLVE_ENVS
       build it: IDS_JSON=recipe/tb2_evolver/tasks_tmax_evolve50_list.json \\
                 OUT_DIR=recipe/tb2_sft/data/tmax_evolve50 \\
                 bash scripts/tmax/build_tmax_envs_from_taxonomy.sh"
echo "envs    : holdout=$(wc -l <"$HOLDOUT_ENVS") rows, evolve=$(wc -l <"$EVOLVE_ENVS") rows"
[[ -f "$TAXONOMY_PARQUET" ]] || fail "rotation needs TAXONOMY_PARQUET: $TAXONOMY_PARQUET"

# 5. meta-agent key + a live call. A bad key otherwise surfaces only after the
#    first evolve round's 50 rollouts have already burned ~30 minutes.
[[ -n "$SFG_API_KEY" ]] || fail "set SFG_API_KEY (or OPENAI_API_KEY) for the $META_MODEL meta-agent"
if [[ "${SKIP_META_PROBE:-0}" != "1" ]]; then
  echo "meta    : probing $META_MODEL via $GATEWAY_URL ..."
  META_MODEL="$META_MODEL" PROVIDER_ID="$PROVIDER_ID" GATEWAY_URL="$GATEWAY_URL" \
  SFG_API_KEY="$SFG_API_KEY" OPENAI_API_KEY="$OPENAI_API_KEY" \
  "$PYTHON_BIN" - <<'PY' || fail "meta-agent probe failed — fix the key/model/gateway before starting a multi-day run (SKIP_META_PROBE=1 to bypass)"
import os
os.environ["OPENAI_API_BASE"] = os.environ["GATEWAY_URL"].rstrip("/") + "/openai/process/v1"
import litellm
r = litellm.completion(
    model=os.environ["META_MODEL"],
    messages=[{"role": "user", "content": "reply with the single word: ok"}],
    max_tokens=16,
    api_key=os.environ.get("OPENAI_API_KEY") or os.environ["SFG_API_KEY"],
    extra_headers={
        "X-Api-Key": os.environ["SFG_API_KEY"],
        "X-Model-Provider-Id": os.environ["PROVIDER_ID"],
    },
)
print("  meta-agent OK ->", (r.choices[0].message.content or "").strip()[:40])
PY
fi

echo
echo "===== plan ====="
printf '  %-22s %s\n' \
  "base model"     "Qwen3.5-${MODEL_SIZE} (+LoRA, 8 vLLM replicas)" \
  "meta-agent"     "$META_MODEL via $GATEWAY_URL" \
  "REPLICATE"      "$REPLICATE" \
  "iterations"     "$N_ITERS x (${EVOLVE_ROUNDS} evolve rounds, <=${MAX_SFT_RETRIES} SFT retries)" \
  "evolve set"     "${EVOLVE_SET_SIZE} tasks, rotating from iter ${ROTATE_FROM_ITER}" \
  "holdout"        "102 tasks, concurrency ${HOLDOUT_CONCURRENT}" \
  "rollout conc."  "$TMAX_CONCURRENT" \
  "ratchet"        "ACCEPT_TIES=$ACCEPT_TIES harness_ratchet=$HARNESS_RATCHET" \
  "SFT"            "seq=$SFT_MAX_SEQ_LENGTH eff_batch=$SFT_EFFECTIVE_BATCH epochs=$SFT_EPOCHS attn=$SFT_ATTN_IMPLEMENTATION" \
  "images"         "shared_base=$SHARED_BASE${TMAX_SHARED_BASE_TAG:+ ($TMAX_SHARED_BASE_TAG)}" \
  "state"          "outputs/tmax_coevolve/rep${REPLICATE}/" \
  "log"            "$RUN_LOG"
echo

if [[ "${DETACH:-0}" == "1" ]]; then
  setsid nohup bash "$ROOT/scripts/tmax/run_loop_tmax_coevolve.sh" >"$RUN_LOG" 2>&1 &
  echo "detached pid=$! log=$RUN_LOG"
  echo "watch:  tail -f $RUN_LOG"
  echo "status: cat outputs/tmax_coevolve/rep${REPLICATE}/STATUS"
else
  exec bash "$ROOT/scripts/tmax/run_loop_tmax_coevolve.sh" 2>&1 | tee "$RUN_LOG"
fi
