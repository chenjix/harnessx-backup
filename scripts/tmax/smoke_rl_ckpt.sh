#!/usr/bin/env bash
# Cheap smoke for the RL init checkpoint, upstream of grpo_fast.
#
# Why: the rl dry-runs died in vLLM engine init with
#   AttributeError: 'Qwen3_5TextConfig' object has no attribute 'vision_config'
# but only after ray + a 4-actor sandbox pool were up (~4 min per attempt), and
# with no artifacts left behind. Qwen3.5 is a VLM shell; a bare merged text
# tower is unservable because vLLM 0.24 only registers
# Qwen3_5ForConditionalGeneration. This script gates that in seconds.
#
# Stages:
#   1) static config/weight audit          (CPU, ~1s, no GPU needed)
#   2) optional re-merge + re-audit        (CPU, minutes)   REMERGE=1
#   3) real vLLM boot + 8-token generate   (1 GPU, ~2 min)  SKIP_BOOT=1 to skip
#
# Stage 1 alone runs on the login node. Stages 2-3 need a GPU allocation:
#
#   srun --account=interactive-ai --partition=ml.p5en.48xlarge --nodes=1 \
#        --gres=gpu:h200:1 --cpus-per-task=12 --mem=0 --time=1:00:00 \
#        --job-name=rl-ckpt-smoke \
#        bash scripts/tmax/smoke_rl_ckpt.sh 2>&1 | tee logs/rl_ckpt_smoke.log
#
# Env:
#   CKPT_DIR    checkpoint to validate (default: the dry-run's init model)
#   ADAPTER_DIR SFT LoRA adapter, used by REMERGE and to resolve BASE_MODEL
#   BASE_MODEL  base weights (default: adapter_config.json base_model_name_or_path)
#   REMERGE=1   re-merge into REMERGE_OUT when stage 1 fails, then re-audit
#   SKIP_BOOT=1 stop after the static audit (no GPU required)
#   DEEP=1      also prove the LoRA merge changed weights (reads tensor slices;
#               slow on the login node, where fsx makes even a venv python
#               start take ~60s -- prefer running it on a compute node)
set -Eeuo pipefail

_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"
source "$_HX_SCRIPTS/_common.sh"

PY="${RL_PYTHON:-${SFT_PYTHON:-${PYTHON_BIN:-$(python_bin)}}}"
# The audit is stdlib-only, so run it under whichever interpreter starts fastest;
# the project venv lives on fsx and can take a minute just to boot on a login node.
AUDIT_PY="${AUDIT_PY:-$(command -v python3 || echo "$PY")}"

ADAPTER_DIR="${ADAPTER_DIR:-$ROOT/outputs/sft/tmax_coev_rep1_i1}"
CKPT_DIR="${CKPT_DIR:-$ROOT/outputs/rl/merged/tmax_rl_smoke_1948}"
REMERGE="${REMERGE:-0}"
DEEP="${DEEP:-0}"
SKIP_BOOT="${SKIP_BOOT:-0}"
BOOT_MAX_LEN="${BOOT_MAX_LEN:-4096}"
BOOT_UTIL="${BOOT_UTIL:-0.40}"

log() { printf '\n\033[1m[rl-ckpt-smoke] %s\033[0m\n' "$*"; }
die() { echo "ERROR: $*" >&2; exit 2; }

# BASE_MODEL: prefer the adapter's own record over $MODEL, so the audit compares
# against the exact weights the adapter was trained on.
if [[ -z "${BASE_MODEL:-}" && -f "$ADAPTER_DIR/adapter_config.json" ]]; then
  BASE_MODEL="$("$PY" -c "
import json,sys
print(json.load(open('$ADAPTER_DIR/adapter_config.json')).get('base_model_name_or_path') or '')
" 2>/dev/null || true)"
fi
BASE_MODEL="${BASE_MODEL:-${MODEL:-}}"

REMERGE_OUT="${REMERGE_OUT:-$ROOT/outputs/rl/merged/$(basename "$ADAPTER_DIR")_shell}"

log "plan"
echo "  python   : $PY"
echo "  audit py : $AUDIT_PY"
echo "  ckpt     : $CKPT_DIR"
echo "  adapter  : $ADAPTER_DIR"
echo "  base     : ${BASE_MODEL:-<unresolved>}"
echo "  remerge  : $REMERGE  (out=$REMERGE_OUT)"
echo "  deep     : $DEEP"
echo "  boot     : $([[ "$SKIP_BOOT" == 1 ]] && echo skipped || echo "yes (1 GPU, max_len=$BOOT_MAX_LEN util=$BOOT_UTIL)")"

audit_args=("$_HX_SCRIPTS/tmax/check_rl_ckpt.py")
# if-blocks, not `[[ ... ]] && arr+=(...)`: under `set -e` a false test as the
# last command of the list would abort the script.
if [[ -n "$BASE_MODEL" ]]; then
  audit_args+=(--base "$BASE_MODEL")
  if [[ "$DEEP" == "1" ]]; then
    audit_args+=(--deep)
  fi
fi

log "stage 1: static audit of $CKPT_DIR"
audit_rc=0
"$AUDIT_PY" "${audit_args[0]}" "$CKPT_DIR" "${audit_args[@]:1}" || audit_rc=$?

if (( audit_rc != 0 )); then
  if [[ "$REMERGE" != "1" ]]; then
    log "stage 1 FAILED"
    echo "Re-run with REMERGE=1 to rebuild the checkpoint from $ADAPTER_DIR:" >&2
    echo "  REMERGE=1 ADAPTER_DIR=$ADAPTER_DIR bash scripts/tmax/smoke_rl_ckpt.sh" >&2
    exit 2
  fi

  log "stage 2: re-merging $ADAPTER_DIR -> $REMERGE_OUT"
  [[ -n "$BASE_MODEL" ]] || die "cannot re-merge without BASE_MODEL"
  BASE_MODEL="$BASE_MODEL" ADAPTER_DIR="$ADAPTER_DIR" OUTPUT_DIR="$REMERGE_OUT" \
    bash "$_HX_SCRIPTS/tmax/merge_sft_adapter.sh"

  CKPT_DIR="$REMERGE_OUT"
  log "stage 2: re-auditing $CKPT_DIR"
  "$AUDIT_PY" "${audit_args[0]}" "$CKPT_DIR" "${audit_args[@]:1}" \
    || die "re-merged checkpoint still fails the audit -- fix merge_sft_adapter.sh"
else
  log "stage 1 PASSED"
fi

if [[ "$SKIP_BOOT" == "1" ]]; then
  log "done (static audit only)"
  echo "RL_INIT_MODEL=$CKPT_DIR"
  exit 0
fi

command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L || echo "WARNING: no nvidia-smi" >&2

# A leftover engine from an earlier attempt would show up as an OOM here, not as
# the config error we are chasing.
pkill -f 'vllm.entrypoints.openai.api_server' 2>/dev/null || true
ray stop --force >/dev/null 2>&1 || true
sleep 3

log "stage 3: vLLM boot on $CKPT_DIR"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" \
VLLM_DISABLE_COMPILE_CACHE=1 \
  "$PY" "$_HX_SCRIPTS/tmax/boot_vllm_ckpt.py" "$CKPT_DIR" \
    --max-model-len "$BOOT_MAX_LEN" --util "$BOOT_UTIL" \
  || die "vLLM cannot serve $CKPT_DIR -- do not launch grpo_fast with it"

log "ALL STAGES PASSED"
cat <<EOS

Use this checkpoint for the RL dry-run:

  RL_INIT_MODEL=$CKPT_DIR \\
  DRYRUN_NAME=tmax_rl_smoke_dryrun4 \\
    bash scripts/tmax/dryrun_rl_grpo.sh
EOS
