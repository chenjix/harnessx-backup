#!/usr/bin/env bash
set -euo pipefail
# Run ONE cell of the {base, SFT} x {baseline harness, evolved harness} 2x2 over
# the full 89-task TB2 set, using all 8 GPUs of the current machine.
#
# One cell per machine: the four cells are independent, so hand each machine a
# different cell name and they finish in one wall-clock window.
#
# Usage:
#   bash scripts/run_cell_89.sh <cell> [round]
#
#   cell  = base-baseline | base-evolved | sft-baseline | sft-evolved
#   round = replicate index, default 1 (only needed if you want >1 round)
#
# Env overrides:
#   SFT_ARM=routed400|mix|pure150   which adapter the sft-* cells use (default routed400)
#   GPU_POOL="0,1,2,3,4,5,6,7"      GPUs to shard across
#   TB2_CONCURRENT=3                concurrent episodes PER replica (see note below)
#   EVOLVE_RUN_TAG=<tag>            which evolve run supplies the evolved harness

# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

CELL="${1:?usage: run_cell_89.sh <base-baseline|base-evolved|sft-baseline|sft-evolved> [round]}"
ROUND="${2:-1}"

export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export MODEL_SIZE="${MODEL_SIZE:-9b}"
export TASKS_JSON="$ROOT/recipe/tb2_evolver/tasks_all_tb2.json"

# 89 tasks will actually saturate every concurrency slot, unlike the 15-task set
# where only ~2 episodes per replica were ever live. Five concurrent long-context
# episodes on one 40 GB card is real KV-cache pressure, and a KV eviction shows up
# as a degraded episode, not as a crash — i.e. a silently worse score. Default to
# 3 per replica (24 in flight across 8 GPUs), which still keeps the fleet busy.
export TB2_CONCURRENT="${TB2_CONCURRENT:-3}"
export TB2_MAX_STEPS="${TB2_MAX_STEPS:-120}"

EVOLVE_RUN_TAG="${EVOLVE_RUN_TAG:-tb2-qwen35-9b-tmax-20260802-091457}"
SFT_ARM="${SFT_ARM:-routed400}"
case "$SFT_ARM" in
  routed400) ADAPTER="$ROOT/outputs/sft/qwen35_9b_tmax_routed400" ;;
  mix)       ADAPTER="$ROOT/outputs/sft/qwen35_9b_qwen35_9b_own_success_plus_tmax100" ;;
  pure150)   ADAPTER="$ROOT/outputs/sft/qwen35_9b_tmax_only150" ;;
  *) echo "ERROR: unknown SFT_ARM='$SFT_ARM' (routed400|mix|pure150)" >&2; exit 2 ;;
esac

BASELINE_HARNESS="$ROOT/configs/baseline_harness.yaml"

# Resolve the evolved harness to the GATED best_so_far config, explicitly.
# evaluate.sh can look this up itself, but only from the *current* RUN_TAG's
# evolve state — and these cells run under their own tags, so the lookup would
# miss and (without the guard added earlier) silently fall back to the baseline
# harness, making the "evolved" column a duplicate of the baseline one.
resolve_evolved() {
  local st="$ROOT/recipe/tb2_evolver/runs/$EVOLVE_RUN_TAG/_meta_v2/_meta_scratch/harness_evolve_state.json"
  [[ -f "$st" ]] || { echo "ERROR: no evolve state at $st" >&2; exit 2; }
  "$(command -v python3)" - "$st" <<'PY'
import json, sys, os
s = json.load(open(sys.argv[1]))
b = s.get("best_so_far") or {}
cfg = b.get("config")
if not cfg or not os.path.isfile(cfg):
    sys.exit(f"best_so_far config missing or unreadable: {cfg}")
print(cfg)
PY
}

case "$CELL" in
  base-baseline)
    export EVAL_SFT=0 USE_EVOLVED_HARNESS=0
    export HARNESS_CONFIG="$BASELINE_HARNESS"
    LABEL="base + baseline harness"
    ;;
  base-evolved)
    export EVAL_SFT=0 USE_EVOLVED_HARNESS=0   # config passed explicitly below
    HARNESS_CONFIG="$(resolve_evolved)"; export HARNESS_CONFIG
    LABEL="base + evolved harness"
    ;;
  sft-baseline)
    export EVAL_SFT=1 USE_EVOLVED_HARNESS=0
    export HARNESS_CONFIG="$BASELINE_HARNESS"
    export LORA_PATH="$ADAPTER" LORA_NAME="qwen35-9b-$SFT_ARM"
    LABEL="SFT($SFT_ARM) + baseline harness"
    ;;
  sft-evolved)
    export EVAL_SFT=1 USE_EVOLVED_HARNESS=0
    HARNESS_CONFIG="$(resolve_evolved)"; export HARNESS_CONFIG
    export LORA_PATH="$ADAPTER" LORA_NAME="qwen35-9b-$SFT_ARM"
    LABEL="SFT($SFT_ARM) + evolved harness"
    ;;
  *) echo "ERROR: unknown cell '$CELL'" >&2; exit 2 ;;
esac

# /fsx is shared across the machines, so the tag must be unique per cell —
# otherwise two machines overwrite each other's endpoints.json and logs.
SUFFIX=""
[[ "$CELL" == sft-* ]] && SUFFIX="-$SFT_ARM"
export RUN_TAG="full89-${CELL}${SUFFIX}-r${ROUND}"
export JOB_NAME="$RUN_TAG"

# ── preflight: fail now, not 2 hours in ──────────────────────────────────────
[[ -f "$HARNESS_CONFIG" ]] || { echo "ERROR: harness config not found: $HARNESS_CONFIG" >&2; exit 2; }
if [[ "${EVAL_SFT:-0}" == "1" ]]; then
  [[ -d "$LORA_PATH" ]] || { echo "ERROR: adapter not found: $LORA_PATH" >&2; exit 2; }
  ls "$LORA_PATH"/adapter_model.safetensors >/dev/null 2>&1 \
    || ls "$LORA_PATH"/adapter_model.bin >/dev/null 2>&1 \
    || { echo "ERROR: no adapter weights in $LORA_PATH" >&2; exit 2; }
fi
if [[ -d "$ROOT/.benchmarks/tb2/$JOB_NAME" ]]; then
  echo "NOTE: $JOB_NAME already exists; sharded_tb2_eval rebuilds it from scratch." >&2
fi

# Delete each task's image after it runs. Without this, 89 distinct multi-GB
# images accumulate and the pull fails partway through — observed as 45/89 tasks
# dying with `RuntimeError: Docker compose ... Pulling` and n_input_tokens=0,
# which scores as 2/89 and looks like a catastrophically bad model.
export TB2_DELETE_IMAGES="${TB2_DELETE_IMAGES:-1}"
# Periodic sweep on top of the per-task delete: reclaims dangling layers, images
# orphaned by a task that died before teardown, and the build cache. Also fires
# on demand whenever free space dips under the floor.
export TB2_PRUNE_EVERY="${TB2_PRUNE_EVERY:-8}"
export TB2_MIN_FREE_GB="${TB2_MIN_FREE_GB:-30}"

# The disk that matters is Docker's store — normally the ROOT filesystem, which
# is small — not the shared /fsx where results land. Checking only /fsx (which
# has terabytes) is what let the previous attempt start on a 96%-full root disk.
# Image layers stream into containerd's content store, which is configured
# independently of DockerRootDir — a host can have DockerRootDir on a multi-TB
# NVMe while containerd sits on a nearly-full root filesystem. Checking only
# DockerRootDir is what let a run start and then lose 63/89 tasks to
# `no space left on device` under /var/lib/containerd.
docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
containerd_root="$(sed -nE 's/^[[:space:]]*root[[:space:]]*=[[:space:]]*"?([^"]+)"?.*/\1/p' \
                    /etc/containerd/config.toml 2>/dev/null | head -1)"
containerd_root="${containerd_root:-/var/lib/containerd}"
# Peak transient space is (simultaneous pulls) x (image size), not the total of
# all 89 images: per-task deletion bounds accumulation but not concurrency. So the
# requirement scales with TB2_CONCURRENT rather than being a fixed number, and
# when space is tight the fix is fewer parallel pulls (or a bigger disk), never
# more pruning.
N_GPUS=$(awk -F',' '{print NF}' <<<"$GPU_POOL")
PEAK_PULLS=$(( N_GPUS * TB2_CONCURRENT ))
# Empirical: TB2 base images are slim (python:3.13-slim / ubuntu:24.04, ~150-200MB)
# and a 15-task eval with up to 40 concurrent slots ran clean while /var/lib/containerd
# hovered at 25-30G free — so transient cost is ~1-2G per pull, not 4G. Override with
# TB2_GB_PER_IMAGE once you have measured it on your own host.
GB_PER_IMAGE="${TB2_GB_PER_IMAGE:-2}"
NEED_IMG=$(( PEAK_PULLS * GB_PER_IMAGE ))
for mount in "$containerd_root" "$docker_root" /fsx/home; do
  avail_gb=$(df -BG --output=avail "$mount" 2>/dev/null | tail -1 | tr -dc '0-9')
  [[ -n "$avail_gb" ]] || continue
  need="$NEED_IMG"; [[ "$mount" == "/fsx/home" ]] && need=5
  if [[ "$avail_gb" -lt "$need" ]]; then
    echo "ERROR: ${avail_gb}G free on $mount, need >=${need}G" >&2
    if [[ "$mount" != "/fsx/home" ]]; then
      safe=$(( avail_gb / GB_PER_IMAGE / N_GPUS ))
      echo "       Image layers stream into this filesystem. Up to $PEAK_PULLS pulls can be" >&2
      echo "       in flight at once (${N_GPUS} GPUs x TB2_CONCURRENT=${TB2_CONCURRENT}), ~${GB_PER_IMAGE}G each." >&2
      echo "" >&2
      if [[ "$safe" -ge 1 ]]; then
        echo "       Option A — run now at reduced concurrency (slower, but fits):" >&2
        echo "           TB2_CONCURRENT=$safe bash scripts/run_cell_89.sh $CELL $ROUND" >&2
      else
        echo "       Option A — not viable: even 1 pull per GPU needs $(( N_GPUS * GB_PER_IMAGE ))G." >&2
      fi
      echo "       Option B — reclaim space:" >&2
      echo "           docker system prune -af --volumes && sudo ctr -n moby content prune references" >&2
      echo "       Option C — move containerd's store to a larger disk (permanent fix):" >&2
      echo "           see  df -h  for a roomy mount, then set root= in /etc/containerd/config.toml" >&2
    fi
    exit 2
  fi
  echo "disk ok  : $mount has ${avail_gb}G free (need ${need}G for $PEAK_PULLS concurrent pulls)"
done

echo "================================================================"
echo "cell     : $CELL   ($LABEL)"
echo "round    : $ROUND"
echo "tasks    : $(python3 -c "import json;print(len(json.load(open('$TASKS_JSON'))))") (full TB2)"
echo "harness  : $HARNESS_CONFIG"
[[ "${EVAL_SFT:-0}" == "1" ]] && echo "adapter  : $LORA_PATH"
echo "GPUs     : $GPU_POOL   concurrent/replica=$TB2_CONCURRENT   max_steps=$TB2_MAX_STEPS"
echo "job dir  : .benchmarks/tb2/$JOB_NAME"
echo "================================================================"

exec bash "$ROOT/scripts/evaluate.sh"
