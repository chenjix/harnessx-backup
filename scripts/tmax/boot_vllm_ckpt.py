#!/usr/bin/env python
"""Boot vLLM on a checkpoint and generate a few tokens. Nothing else.

This is stage 2 of the RL checkpoint smoke: it reproduces (or clears) the exact
vLLM engine-init failure that the dry-runs only hit after ~4 minutes of ray and
sandbox-pool startup, using one GPU and no ray/no environments.

Usage:
  python scripts/tmax/boot_vllm_ckpt.py CKPT_DIR [--max-model-len N] [--util F]
"""

from __future__ import annotations

import argparse
import sys
import traceback


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ckpt")
    ap.add_argument("--max-model-len", type=int, default=4096)
    ap.add_argument("--util", type=float, default=0.40)
    ap.add_argument("--max-tokens", type=int, default=8)
    args = ap.parse_args()

    print(f"booting vLLM on {args.ckpt}", flush=True)
    try:
        from vllm import LLM, SamplingParams
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: cannot import vllm: {exc}", file=sys.stderr)
        return 2
    import vllm

    print(f"  vllm {vllm.__version__}", flush=True)

    try:
        llm = LLM(
            model=args.ckpt,
            tokenizer=args.ckpt,
            max_model_len=args.max_model_len,
            gpu_memory_utilization=args.util,
            tensor_parallel_size=1,
            enforce_eager=True,          # skip inductor/cudagraph: we want load errors, fast
            trust_remote_code=True,
            disable_log_stats=True,
        )
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print(
            "\nFAIL: vLLM could not build the model from this checkpoint.\n"
            "If the trace ends in \"'Qwen3_5TextConfig' object has no attribute "
            "'vision_config'\", the checkpoint is a bare text tower -- re-run with "
            "REMERGE=1 so merge_sft_adapter.sh rebuilds it as a VLM shell.",
            file=sys.stderr,
        )
        return 2

    print("engine up; generating", flush=True)
    try:
        outs = llm.generate(
            ["Reply with the single word: ok"],
            SamplingParams(max_tokens=args.max_tokens, temperature=0.0),
        )
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        print("\nFAIL: engine loaded but generation raised", file=sys.stderr)
        return 2

    text = outs[0].outputs[0].text
    ntok = len(outs[0].outputs[0].token_ids)
    print(f"  sample output ({ntok} tokens): {text!r}")
    if ntok == 0:
        print("\nFAIL: generated zero tokens", file=sys.stderr)
        return 2

    print("\nPASS: checkpoint loads and generates under vLLM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
