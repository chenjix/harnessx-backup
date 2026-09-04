#!/usr/bin/env python3
"""Attach a trained Qwen3.5 text tower back to its servable VLM shell."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-checkpoint", required=True)
    ap.add_argument("--base-shell", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    from transformers import AutoConfig, AutoModelForCausalLM, AutoModelForImageTextToText, AutoProcessor

    text_cfg = AutoConfig.from_pretrained(args.text_checkpoint, trust_remote_code=True)
    if getattr(text_cfg, "vision_config", None) is not None:
        raise SystemExit("checkpoint is already a full shell; no reattachment needed")
    base_cfg = AutoConfig.from_pretrained(args.base_shell, trust_remote_code=True)
    if getattr(base_cfg, "vision_config", None) is None:
        raise SystemExit("base-shell has no vision_config")

    text = AutoModelForCausalLM.from_pretrained(
        args.text_checkpoint, trust_remote_code=True, device_map="cpu", low_cpu_mem_usage=True
    )
    shell = AutoModelForImageTextToText.from_pretrained(
        args.base_shell, trust_remote_code=True, device_map="cpu", low_cpu_mem_usage=True
    )
    source = text.state_dict()
    target = shell.state_dict()
    mapped = {}
    missing = []
    for key, value in source.items():
        candidates = (f"model.language_model.{key.removeprefix('model.')}", key)
        dest = next((k for k in candidates if k in target and target[k].shape == value.shape), None)
        if dest is None:
            missing.append(key)
        else:
            mapped[dest] = value
    if missing or not mapped:
        raise SystemExit(f"text weights do not map into shell: mapped={len(mapped)} missing={missing[:8]}")
    shell.load_state_dict(mapped, strict=False, assign=True)
    args.output.mkdir(parents=True, exist_ok=True)
    shell.save_pretrained(args.output, safe_serialization=True)
    AutoProcessor.from_pretrained(args.base_shell, trust_remote_code=True).save_pretrained(args.output)
    print(json.dumps({"mapped_tensors": len(mapped), "output": str(args.output)}))


if __name__ == "__main__":
    main()
