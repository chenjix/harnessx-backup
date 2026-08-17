#!/usr/bin/env bash
# Merge a PEFT LoRA adapter into the base HF model so open-instruct GRPO/DPPO
# can warm-start from coevolve SFT (official RL expects full weights).
#
#   BASE_MODEL=Qwen/Qwen3.5-9B \
#   ADAPTER_DIR=outputs/sft/tmax_coev_rep1_i1 \
#   OUTPUT_DIR=outputs/rl/merged/tmax_coev_rep1_i1 \
#   bash scripts/merge_sft_adapter.sh
set -euo pipefail
source "$(dirname "$0")/_common.sh"

ADAPTER_DIR="${ADAPTER_DIR:?set ADAPTER_DIR to a LoRA adapter directory}"
OUTPUT_DIR="${OUTPUT_DIR:?set OUTPUT_DIR for the merged HF checkpoint}"
BASE_MODEL="${BASE_MODEL:-$MODEL}"
PY="${SFT_PYTHON:-${PYTHON_BIN:-$(python_bin)}}"

[[ -d "$ADAPTER_DIR" ]] || { echo "ERROR: ADAPTER_DIR not a directory: $ADAPTER_DIR" >&2; exit 2; }
ls "$ADAPTER_DIR"/adapter_model.safetensors >/dev/null 2>&1 \
  || ls "$ADAPTER_DIR"/adapter_model.bin >/dev/null 2>&1 \
  || { echo "ERROR: no adapter weights under $ADAPTER_DIR" >&2; exit 2; }

mkdir -p "$(dirname "$OUTPUT_DIR")"
echo "Merging LoRA → full weights"
echo "  base    : $BASE_MODEL"
echo "  adapter : $ADAPTER_DIR"
echo "  output  : $OUTPUT_DIR"

export BASE_MODEL ADAPTER_DIR OUTPUT_DIR
"$PY" - <<'PY'
import os
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

base = os.environ["BASE_MODEL"]
adapter = os.environ["ADAPTER_DIR"]
out = os.environ["OUTPUT_DIR"]

print(f"loading base {base}")
tok = AutoTokenizer.from_pretrained(adapter, trust_remote_code=True)
# Prefer adapter tokenizer (chat template) when present; fall back to base.
if tok.chat_template is None:
    tok = AutoTokenizer.from_pretrained(base, trust_remote_code=True)

model = AutoModelForCausalLM.from_pretrained(
    base,
    torch_dtype=torch.bfloat16,
    trust_remote_code=True,
    device_map="cpu",
)
print(f"loading adapter {adapter}")
model = PeftModel.from_pretrained(model, adapter)
print("merge_and_unload…")
model = model.merge_and_unload()
Path(out).mkdir(parents=True, exist_ok=True)
model.save_pretrained(out, safe_serialization=True)
tok.save_pretrained(out)
print(f"wrote merged model → {out}")
PY

echo "Merged OK: $OUTPUT_DIR"
