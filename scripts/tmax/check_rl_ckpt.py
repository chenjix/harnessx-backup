#!/usr/bin/env python
"""Validate a merged checkpoint before handing it to open-instruct / vLLM.

Catches the failure mode that killed the rl dry-runs: Qwen3.5 ships as a VLM
shell (vision tower + `model.language_model` text tower), but a naive
`merge_and_unload().save_pretrained()` writes only the bare text tower. That
config has no `vision_config`, and vLLM 0.24 has no registry entry for
`Qwen3_5ForCausalLM` -- it only knows `Qwen3_5ForConditionalGeneration`, whose
__init__ reads `config.vision_config` unconditionally and dies with

    AttributeError: 'Qwen3_5TextConfig' object has no attribute 'vision_config'

after ~4 minutes of ray + sandbox-pool startup. Everything here is cheap
(json + safetensors headers, no torch, no weights) so RL fails in seconds.

Usage:
  python scripts/tmax/check_rl_ckpt.py CKPT_DIR [--base BASE_MODEL] [--deep]

Exit 0 = servable, 2 = rejected.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

PROBLEMS: list[str] = []
NOTES: list[str] = []


def fail(msg: str) -> None:
    PROBLEMS.append(msg)


def note(msg: str) -> None:
    NOTES.append(msg)


def load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        fail(f"cannot read {path}: {exc}")
        return None


def safetensors_keys(ckpt: Path) -> set[str]:
    """Weight names from the shard index, or from safetensors headers directly.

    A safetensors file starts with u64-LE header length then a JSON header, so
    the key list costs one small read per shard -- no torch, no weights.
    """
    index = ckpt / "model.safetensors.index.json"
    if index.is_file():
        data = load_json(index) or {}
        return set((data.get("weight_map") or {}).keys())

    keys: set[str] = set()
    shards = sorted(ckpt.glob("*.safetensors"))
    for shard in shards:
        try:
            with shard.open("rb") as fh:
                (n,) = struct.unpack("<Q", fh.read(8))
                header = json.loads(fh.read(n))
            keys.update(k for k in header if k != "__metadata__")
        except Exception as exc:  # noqa: BLE001
            fail(f"cannot read safetensors header of {shard.name}: {exc}")
    return keys


def _tensor_head(root: Path, names: list[str], nbytes: int = 4096):
    """First bytes of the first matching tensor, read straight from safetensors.

    Pure stdlib on purpose: the audit runs under whichever interpreter starts
    fastest (often the system python3, which has no safetensors/torch), and a
    check that silently skips itself is worse than no check.
    """
    index = root / "model.safetensors.index.json"
    wmap = {}
    if index.is_file():
        wmap = (load_json(index) or {}).get("weight_map") or {}

    for name in names:
        shards = [root / wmap[name]] if name in wmap else sorted(root.glob("*.safetensors"))
        for shard in shards:
            if not shard.is_file():
                continue
            try:
                with shard.open("rb") as fh:
                    (hlen,) = struct.unpack("<Q", fh.read(8))
                    header = json.loads(fh.read(hlen))
                    meta = header.get(name)
                    if not meta:
                        continue
                    begin, stop = meta["data_offsets"]
                    fh.seek(8 + hlen + begin)
                    return name, fh.read(min(nbytes, stop - begin))
            except Exception:  # noqa: BLE001, S112
                continue
    return None, None


def check_weights_differ(ckpt: Path, base: Path, arch_is_shell: bool) -> None:
    """Prove the LoRA merge actually landed: some text weight must differ.

    save_pretrained on a shell built with `load_state_dict(..., strict=False)`
    silently keeps base weights when key prefixes do not line up, so a
    structurally perfect checkpoint can still be a no-op merge.

    Candidates are LoRA target projections, tried in order. Qwen3.5 interleaves
    linear_attn and self_attn blocks, so layer 0 has no self_attn.q_proj -- the
    mlp projections are the ones present in every layer.
    """
    candidates = [
        "layers.0.mlp.down_proj.weight",
        "layers.0.mlp.gate_proj.weight",
        "layers.0.mlp.up_proj.weight",
        "layers.0.self_attn.q_proj.weight",
        "layers.1.self_attn.q_proj.weight",
    ]
    # Shell nests the text tower one level down; a bare text tower does not.
    prefixes = ["model.language_model.", "model."]

    for cand in candidates:
        names = [pre + cand for pre in prefixes]
        ck_name, ck_head = _tensor_head(ckpt, names)
        base_name, base_head = _tensor_head(base, names)
        if ck_head is None or base_head is None:
            continue
        # Both sides must be the same logical tensor, not just any two that matched.
        if ck_name.split(".", 1)[-1].removeprefix("language_model.") != \
           base_name.split(".", 1)[-1].removeprefix("language_model."):
            continue

        if ck_head == base_head:
            fail(
                f"merged weights are byte-identical to base at {ck_name} -- the "
                "LoRA merge was a no-op (check the state_dict key prefixes in "
                "merge_sft_adapter.sh; load_state_dict(strict=False) hides this)"
            )
        else:
            note(f"deep check OK: {ck_name} differs from base")
        return

    note(f"--deep skipped: none of {len(candidates)} candidate tensors found in both checkpoints")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ckpt")
    ap.add_argument("--base", default=None, help="base model dir to compare against")
    ap.add_argument(
        "--deep",
        action="store_true",
        help="also prove the LoRA merge changed weights (needs --base)",
    )
    args = ap.parse_args()

    ckpt = Path(args.ckpt)
    print(f"checking {ckpt}")
    if not ckpt.is_dir():
        print(f"FAIL: not a directory: {ckpt}", file=sys.stderr)
        return 2

    cfg_path = ckpt / "config.json"
    if not cfg_path.is_file():
        print(f"FAIL: missing {cfg_path}", file=sys.stderr)
        return 2
    cfg = load_json(cfg_path) or {}

    arch = (cfg.get("architectures") or [None])[0]
    model_type = cfg.get("model_type")
    has_vision = cfg.get("vision_config") is not None
    print(f"  architectures : {arch}")
    print(f"  model_type    : {model_type}")
    print(f"  vision_config : {'present' if has_vision else 'MISSING'}")

    base_cfg = None
    base_is_shell = None
    if args.base:
        base_path = Path(args.base)
        base_cfg_path = base_path / "config.json"
        if base_cfg_path.is_file():
            base_cfg = load_json(base_cfg_path) or {}
            base_is_shell = base_cfg.get("vision_config") is not None
            print(
                f"  base          : {base_cfg.get('model_type')} "
                f"/ {(base_cfg.get('architectures') or [None])[0]} "
                f"(shell={base_is_shell})"
            )
        else:
            note(f"base config not found at {base_cfg_path}; skipping base comparison")

    # --- the actual gate -------------------------------------------------
    # vLLM resolves models by architectures[0]. Qwen3_5ForCausalLM is not in
    # vllm/model_executor/models/registry.py -- only the ...ForConditionalGeneration
    # shell is -- so a bare text tower is unservable no matter how valid it looks.
    if arch == "Qwen3_5ForCausalLM" or model_type == "qwen3_5_text":
        fail(
            "bare Qwen3.5 text tower: vLLM 0.24 registry has no "
            "'Qwen3_5ForCausalLM' entry, only 'Qwen3_5ForConditionalGeneration', "
            "which reads config.vision_config unconditionally "
            "(qwen3_5.py:589). Re-merge with the VLM-shell path in "
            "scripts/tmax/merge_sft_adapter.sh."
        )

    if base_is_shell and not has_vision:
        fail(
            "base is a VLM shell but the merged config has no vision_config -- "
            "the merge saved the text tower instead of re-attaching it to the shell"
        )

    keys = safetensors_keys(ckpt)
    if not keys:
        fail("no safetensors weights found (need model.safetensors or a shard index)")
    else:
        print(f"  weight tensors: {len(keys)}")

    if has_vision:
        text_keys = {k for k in keys if k.startswith("model.language_model.")}
        vis_keys = {k for k in keys if k.startswith("model.visual.")}
        print(f"  text tower    : {len(text_keys)} tensors under model.language_model.")
        print(f"  vision tower  : {len(vis_keys)} tensors under model.visual.")
        if not text_keys:
            fail(
                "shell config but no 'model.language_model.*' weights -- the merged "
                "text tower did not land (load_state_dict(strict=False) drops "
                "mismatched keys silently)"
            )
        if not vis_keys:
            fail("shell config but no 'model.visual.*' weights -- vLLM will fail to load the vision tower")
        # vLLM builds the multimodal shell through AutoProcessor. transformers 5.x
        # saves one consolidated processor_config.json with nested image_processor /
        # video_processor sections; 4.x split it into preprocessor_config.json (+
        # video_preprocessor_config.json). Either layout is loadable, so accept both
        # and only insist that an image processor is described somewhere.
        proc_new = ckpt / "processor_config.json"
        proc_old = ckpt / "preprocessor_config.json"
        has_image_proc = proc_old.is_file()
        if not has_image_proc and proc_new.is_file():
            pc = load_json(proc_new) or {}
            has_image_proc = bool(pc.get("image_processor") or pc.get("image_processor_type"))
        if not has_image_proc:
            fail(
                "no image processor config (need preprocessor_config.json, or a "
                "processor_config.json carrying an image_processor section) -- vLLM "
                "cannot construct the multimodal shell without it"
            )

    # Rollouts are tool-calling; a missing chat template silently changes the prompt.
    tok_cfg = ckpt / "tokenizer_config.json"
    if not tok_cfg.is_file():
        fail("missing tokenizer_config.json")
    else:
        tc = load_json(tok_cfg) or {}
        has_tpl = bool(tc.get("chat_template")) or (ckpt / "chat_template.jinja").is_file()
        if not has_tpl:
            fail(
                "no chat template (tokenizer_config.chat_template or "
                "chat_template.jinja) -- RL rollouts need the SFT tool-call format"
            )

    if args.deep:
        if not args.base:
            note("--deep ignored: needs --base")
        else:
            check_weights_differ(ckpt, Path(args.base), has_vision)

    for msg in NOTES:
        print(f"  note: {msg}")

    if PROBLEMS:
        print("\nFAIL: checkpoint is not servable", file=sys.stderr)
        for i, msg in enumerate(PROBLEMS, 1):
            print(f"  {i}. {msg}", file=sys.stderr)
        return 2

    print("\nPASS: checkpoint looks servable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
