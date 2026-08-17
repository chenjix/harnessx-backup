#!/usr/bin/env python3
"""LoRA SFT aligned with official Tmax / open-instruct conventions.

Key differences vs the earlier TRL text-field path:
  - Conversational prompt/completion columns → tokenizer.apply_chat_template
  - completion_only_loss masks the prompt (assistant turns only get gradient)
  - No System:/User: flatten + ChatML double-wrap
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
from dataclasses import dataclass
from typing import Any

import torch
import yaml
from datasets import load_dataset
from peft import LoraConfig, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer


@dataclass
class TrainConfig:
    model_name_or_path: str
    output_dir: str
    dataset_train_file: str
    dataset_eval_file: str
    max_seq_length: int
    packing: bool
    per_device_train_batch_size: int
    per_device_eval_batch_size: int
    gradient_accumulation_steps: int
    num_train_epochs: float
    learning_rate: float
    lr_scheduler_type: str
    warmup_ratio: float
    weight_decay: float
    max_grad_norm: float
    logging_steps: int
    save_steps: int
    eval_steps: int
    save_total_limit: int
    bf16: bool
    gradient_checkpointing: bool
    lora: dict[str, Any]
    attn_implementation: str
    chat_template_style: str = "auto"  # unused for conversational path; kept for yaml compat
    init_adapter: str | None = None
    eval_strategy: str = "steps"
    save_strategy: str = "steps"
    # Official open-instruct / Tmax: loss only on the completion (assistant turn).
    completion_only_loss: bool = True


def load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                return json.loads(s)
            except Exception:
                return value
    return value


def _normalize_tool_arguments(arguments: Any) -> Any:
    """Qwen chat_template does ``arguments|items`` and requires a mapping.

    Trajectory dumps store OpenAI-style JSON *strings*; leave real dicts alone.
    Unparseable strings become ``{"_raw": ...}`` so template still renders.
    """
    if isinstance(arguments, dict):
        return arguments
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        s = arguments.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
        except Exception:
            return {"_raw": arguments}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return arguments


def normalize_messages_for_chat_template(messages: list[Any]) -> list[dict[str, Any]]:
    """Make message lists safe for Qwen/TRL ``apply_chat_template``."""
    out: list[dict[str, Any]] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        msg = dict(raw)
        content = msg.get("content")
        if content is None:
            msg["content"] = ""
        elif not isinstance(content, str):
            msg["content"] = json.dumps(content, ensure_ascii=False)

        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            fixed_calls: list[dict[str, Any]] = []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc = dict(tc)
                if isinstance(tc.get("function"), dict):
                    fn = dict(tc["function"])
                    fn["arguments"] = _normalize_tool_arguments(fn.get("arguments"))
                    if not isinstance(fn.get("name"), str) or not fn["name"]:
                        fn["name"] = str(fn.get("name") or "unknown")
                    tc["function"] = fn
                elif "arguments" in tc:
                    # Flat {name, arguments} form used by some dumps.
                    tc["arguments"] = _normalize_tool_arguments(tc.get("arguments"))
                fixed_calls.append(tc)
            msg["tool_calls"] = fixed_calls
        out.append(msg)
    return out


def to_conversational(example: dict[str, Any]) -> dict[str, Any]:
    """Normalize a row to TRL conversational prompt/completion lists.

    New corpora (after expand_turn_pairs update) already store OpenAI-style
    message lists. Legacy rows with string prompt/response are upgraded to a
    single user→assistant pair so completion_only_loss still applies (format
    is imperfect for legacy rows — rebuild the corpus for full parity).

    Always rewrite tool_call ``arguments`` JSON strings → dicts so Qwen's
    chat template ``arguments|items`` does not TypeError.
    """
    prompt = _parse_maybe_json(example.get("prompt"))
    completion = _parse_maybe_json(example.get("completion"))
    response = example.get("response")

    if isinstance(prompt, list) and prompt and isinstance(prompt[0], dict):
        prompt_n = normalize_messages_for_chat_template(prompt)
        if isinstance(completion, list) and completion and isinstance(completion[0], dict):
            return {
                "prompt": prompt_n,
                "completion": normalize_messages_for_chat_template(completion),
            }
        # completion missing: synthesize from response string
        if isinstance(response, str) and response.strip():
            return {
                "prompt": prompt_n,
                "completion": [{"role": "assistant", "content": response}],
            }

    # Legacy string prompt/response
    p = str(prompt if not isinstance(prompt, list) else example.get("prompt_text") or "")
    r = str(response or "")
    return {
        "prompt": [{"role": "user", "content": p}],
        "completion": [{"role": "assistant", "content": r}],
    }


def _pick_attn(requested: str) -> str:
    """Prefer flash_attn_2 (official) when installed; else fall back."""
    req = (requested or "sdpa").strip()
    if req in ("flash_attention_2", "flash_attn", "fa2"):
        try:
            import flash_attn  # noqa: F401

            return "flash_attention_2"
        except Exception:
            print(
                "warning: flash_attention_2 requested but not importable; using sdpa",
                flush=True,
            )
            return "sdpa"
    return req


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    raw = load_yaml(args.config)
    cfg = TrainConfig(**{k: v for k, v in raw.items() if k in TrainConfig.__dataclass_fields__})
    os.makedirs(cfg.output_dir, exist_ok=True)
    attn = _pick_attn(cfg.attn_implementation)

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            cfg.model_name_or_path, use_fast=True, trust_remote_code=True
        )
    except Exception:
        tokenizer = AutoTokenizer.from_pretrained(
            cfg.model_name_or_path, use_fast=False, trust_remote_code=True
        )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    torch_dtype = torch.bfloat16 if cfg.bf16 else torch.float16
    model = AutoModelForCausalLM.from_pretrained(
        cfg.model_name_or_path,
        torch_dtype=torch_dtype,
        attn_implementation=attn,
        trust_remote_code=True,
    )
    if cfg.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        model.config.use_cache = False

    peft_config = None
    if cfg.init_adapter:
        print(f"continuing from adapter: {cfg.init_adapter}")
        model = PeftModel.from_pretrained(model, cfg.init_adapter, is_trainable=True)
    else:
        targets = cfg.lora["target_modules"]
        peft_config = LoraConfig(
            r=cfg.lora["r"],
            lora_alpha=cfg.lora["alpha"],
            lora_dropout=cfg.lora["dropout"],
            target_modules=targets,
            bias="none",
            task_type="CAUSAL_LM",
        )

    dataset = load_dataset(
        "json",
        data_files={
            "train": cfg.dataset_train_file,
            "eval": cfg.dataset_eval_file,
        },
    )
    # Keep only conversational columns for TRL.
    train_ds = dataset["train"].map(
        to_conversational,
        remove_columns=dataset["train"].column_names,
        desc="to_conversational(train)",
    )
    eval_ds = dataset["eval"].map(
        to_conversational,
        remove_columns=dataset["eval"].column_names,
        desc="to_conversational(eval)",
    )
    sample = train_ds[0]
    print(
        f"[sft] conversational sample: prompt_turns={len(sample['prompt'])} "
        f"completion_turns={len(sample['completion'])} "
        f"completion_only_loss={cfg.completion_only_loss} attn={attn} "
        f"max_seq={cfg.max_seq_length}",
        flush=True,
    )

    base_kwargs: dict[str, Any] = dict(
        output_dir=cfg.output_dir,
        packing=cfg.packing,
        per_device_train_batch_size=cfg.per_device_train_batch_size,
        per_device_eval_batch_size=cfg.per_device_eval_batch_size,
        gradient_accumulation_steps=cfg.gradient_accumulation_steps,
        num_train_epochs=cfg.num_train_epochs,
        learning_rate=cfg.learning_rate,
        lr_scheduler_type=cfg.lr_scheduler_type,
        warmup_ratio=cfg.warmup_ratio,
        weight_decay=cfg.weight_decay,
        max_grad_norm=cfg.max_grad_norm,
        logging_steps=cfg.logging_steps,
        save_steps=cfg.save_steps,
        eval_steps=cfg.eval_steps,
        save_total_limit=cfg.save_total_limit,
        bf16=cfg.bf16,
        gradient_checkpointing=cfg.gradient_checkpointing,
        report_to="none",
        logging_first_step=True,
        completion_only_loss=cfg.completion_only_loss,
    )

    sig = inspect.signature(SFTConfig.__init__)
    supported = set(sig.parameters.keys())
    selected = {k: v for k, v in base_kwargs.items() if k in supported}

    eval_strategy = (cfg.eval_strategy or "steps").lower()
    save_strategy = (cfg.save_strategy or "steps").lower()
    if "eval_strategy" in supported:
        selected["eval_strategy"] = eval_strategy
    elif "evaluation_strategy" in supported:
        selected["evaluation_strategy"] = eval_strategy
    if "save_strategy" in supported:
        selected["save_strategy"] = save_strategy
    if eval_strategy == "epoch":
        selected.pop("eval_steps", None)
    if save_strategy == "epoch":
        selected.pop("save_steps", None)

    # Prefer max_length (newer TRL); fall back to max_seq_length.
    if "max_length" in supported:
        selected["max_length"] = cfg.max_seq_length
    elif "max_seq_length" in supported:
        selected["max_seq_length"] = cfg.max_seq_length

    # Do NOT set dataset_text_field — conversational prompt/completion path.
    selected.pop("dataset_text_field", None)

    training_args = SFTConfig(**selected)

    trainer_kwargs = dict(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
    )
    if peft_config is not None:
        trainer_kwargs["peft_config"] = peft_config
    # Newer TRL uses processing_class=; older used tokenizer=.
    trainer_sig = inspect.signature(SFTTrainer.__init__)
    if "processing_class" in trainer_sig.parameters:
        trainer_kwargs["processing_class"] = tokenizer
    elif "tokenizer" in trainer_sig.parameters:
        trainer_kwargs["tokenizer"] = tokenizer
    else:
        raise TypeError("SFTTrainer accepts neither processing_class nor tokenizer")
    trainer = SFTTrainer(**trainer_kwargs)

    try:
        from transformers import TrainerCallback

        class _EpochReportCallback(TrainerCallback):
            def on_evaluate(self, args, state, control, metrics=None, **kwargs):
                metrics = metrics or {}
                epoch = float(getattr(state, "epoch", 0.0) or 0.0)
                loss = metrics.get("eval_loss", metrics.get("loss"))
                print(
                    f"[epoch_report] epoch={epoch:.2f} step={state.global_step} "
                    f"eval_loss={loss} metrics={metrics}",
                    flush=True,
                )

            def on_epoch_end(self, args, state, control, **kwargs):
                print(
                    f"[epoch_report] epoch_end epoch={float(state.epoch or 0.0):.2f} "
                    f"step={state.global_step}",
                    flush=True,
                )

        trainer.add_callback(_EpochReportCallback())
    except Exception as exc:  # pragma: no cover
        print(f"warning: epoch report callback not attached: {exc}", flush=True)

    resume_env = (os.environ.get("SFT_RESUME") or "").strip()
    resume_arg: Any = None
    if resume_env.lower() in ("1", "true", "yes", "auto"):
        resume_arg = True
    elif resume_env and resume_env not in ("0", "false", "no"):
        resume_arg = resume_env
    if resume_arg:
        print(f"Resuming SFT from checkpoint: {resume_arg!r}", flush=True)
        trainer.train(resume_from_checkpoint=resume_arg)
    else:
        trainer.train()
    trainer.save_model(cfg.output_dir)
    if int(os.environ.get("RANK", "0")) == 0:
        tokenizer.save_pretrained(cfg.output_dir)
        print(f"Saved adapter/tokenizer to: {cfg.output_dir}")


if __name__ == "__main__":
    main()
