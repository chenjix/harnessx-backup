#!/usr/bin/env bash
# Merge a PEFT LoRA adapter into the base HF model so open-instruct GRPO/DPPO
# can warm-start from coevolve SFT (official RL expects full weights).
#
#   BASE_MODEL=Qwen/Qwen3.5-9B \
#   ADAPTER_DIR=outputs/sft/tmax_coev_rep1_i1 \
#   OUTPUT_DIR=outputs/rl/merged/tmax_coev_rep1_i1 \
#   bash scripts/merge_sft_adapter.sh
set -euo pipefail
# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
source "$_HX_SCRIPTS/_common.sh"

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
from transformers import (
    AutoConfig,
    AutoModelForCausalLM,
    AutoModelForImageTextToText,
    AutoProcessor,
    AutoTokenizer,
)

base = os.environ["BASE_MODEL"]
adapter = os.environ["ADAPTER_DIR"]
out = os.environ["OUTPUT_DIR"]

print(f"loading base {base}")
tok = AutoTokenizer.from_pretrained(adapter, trust_remote_code=True)
# Prefer adapter tokenizer (chat template) when present; fall back to base.
if tok.chat_template is None:
    tok = AutoTokenizer.from_pretrained(base, trust_remote_code=True)

base_config = AutoConfig.from_pretrained(base, trust_remote_code=True)
# Qwen3.5 and friends ship as a VLM shell: a vision tower plus a text tower under
# `model.language_model`. SFT only touches the text tower, but vLLM builds the
# whole shell from config.json, so the merged checkpoint has to stay a shell —
# saving the bare text tower yields a config without `vision_config` and vLLM
# dies in Qwen3_5ForConditionalGeneration.__init__.
is_vlm_shell = getattr(base_config, "vision_config", None) is not None

model = AutoModelForCausalLM.from_pretrained(
    base,
    dtype=torch.bfloat16,
    trust_remote_code=True,
    device_map="cpu",
)
print(f"loading adapter {adapter}")
model = PeftModel.from_pretrained(model, adapter)
print("merge_and_unload…")
model = model.merge_and_unload()

Path(out).mkdir(parents=True, exist_ok=True)
if is_vlm_shell:
    print("base is a VLM shell; re-attaching merged text tower to the vision tower")
    text_state = {
        (f"model.language_model.{k[len('model.'):]}" if k.startswith("model.") else k): v
        for k, v in model.state_dict().items()
    }
    del model
    shell = AutoModelForImageTextToText.from_pretrained(
        base, dtype=torch.bfloat16, trust_remote_code=True, device_map="cpu"
    )
    unexpected = sorted(set(text_state) - set(shell.state_dict()))
    if unexpected:
        raise SystemExit(f"merged text weights do not fit the VLM shell: {unexpected[:8]}")
    shell.load_state_dict(text_state, strict=False, assign=True)
    del text_state
    shell.save_pretrained(out, safe_serialization=True)
    # vLLM needs the image/video processor to build the multimodal shell.
    try:
        AutoProcessor.from_pretrained(base, trust_remote_code=True).save_pretrained(out)
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not save processor from {base}: {exc}")
else:
    model.save_pretrained(out, safe_serialization=True)
# Saved last so the SFT chat template wins over the processor's copy.
tok.save_pretrained(out)
print(f"wrote merged model → {out}")
PY

echo "Merged OK: $OUTPUT_DIR"
