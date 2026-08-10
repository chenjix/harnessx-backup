#!/usr/bin/env python3
"""Small-scale LoRA SFT for Qwen/Gemma on filtered TB2 trajectories."""

from __future__ import annotations

import argparse
import inspect
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
    chat_template_style: str = "auto"  # auto | qwen | gemma
    # Continue training an existing LoRA instead of starting a fresh one.
    init_adapter: str | None = None
    # "steps" (default) or "epoch" — epoch mode reports eval after every epoch.
    eval_strategy: str = "steps"
    save_strategy: str = "steps"


def load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def detect_style(model_name: str, explicit: str) -> str:
    if explicit and explicit != "auto":
        return explicit
    low = model_name.lower()
    if "gemma" in low:
        return "gemma"
    return "qwen"


def to_text_fn(style: str):
    def _to_text(example: dict[str, Any]) -> dict[str, str]:
        prompt = str(example["prompt"]).strip()
        response = str(example["response"]).strip()
        if style == "gemma":
            text = (
                f"<start_of_turn>user\n{prompt}<end_of_turn>\n"
                f"<start_of_turn>model\n{response}<end_of_turn>"
            )
        else:
            # Generic ChatML-like format that works with Qwen instruct/base SFT.
            text = (
                f"<|im_start|>user\n{prompt}<|im_end|>\n"
                f"<|im_start|>assistant\n{response}<|im_end|>"
            )
        return {"text": text}

    return _to_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    raw = load_yaml(args.config)
    cfg = TrainConfig(**raw)
    os.makedirs(cfg.output_dir, exist_ok=True)
    style = detect_style(cfg.model_name_or_path, cfg.chat_template_style)

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
        attn_implementation=cfg.attn_implementation,
        trust_remote_code=True,
    )
    if cfg.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        model.config.use_cache = False

    peft_config = None
    if cfg.init_adapter:
        # Resume from a previously trained adapter so extra epochs continue
        # from its weights rather than re-initialising LoRA from scratch.
        print(f"continuing from adapter: {cfg.init_adapter}")
        model = PeftModel.from_pretrained(model, cfg.init_adapter, is_trainable=True)
    else:
        # A string target_modules is treated as a regex by PEFT, which is how we
        # scope LoRA to the language model and skip the vision tower (gemma-4
        # wraps those Linears in Gemma4ClippableLinear, unsupported by PEFT).
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
    mapper = to_text_fn(style)
    train_ds = dataset["train"].map(mapper, remove_columns=dataset["train"].column_names)
    eval_ds = dataset["eval"].map(mapper, remove_columns=dataset["eval"].column_names)

    # Compatibility across TRL versions: pass only supported fields.
    base_kwargs: dict[str, Any] = dict(
        output_dir=cfg.output_dir,
        packing=cfg.packing,
        dataset_text_field="text",
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
    # When evaluating/saving by epoch, step-based knobs are unused; keep them only
    # for steps mode so HF/TRL does not warn about ignored arguments.
    if eval_strategy == "epoch":
        selected.pop("eval_steps", None)
    if save_strategy == "epoch":
        selected.pop("save_steps", None)

    if "max_seq_length" in supported:
        selected["max_seq_length"] = cfg.max_seq_length
    elif "max_length" in supported:
        selected["max_length"] = cfg.max_seq_length

    training_args = SFTConfig(**selected)

    trainer_kwargs = dict(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
    )
    if peft_config is not None:
        trainer_kwargs["peft_config"] = peft_config
    # TRL API drift: tokenizer vs processing_class
    try:
        trainer = SFTTrainer(tokenizer=tokenizer, **trainer_kwargs)
    except TypeError:
        trainer = SFTTrainer(processing_class=tokenizer, **trainer_kwargs)

    # Clear per-epoch banner so logs are easy to skim while training runs.
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
    except Exception as exc:  # pragma: no cover - best-effort logging only
        print(f"warning: epoch report callback not attached: {exc}", flush=True)

    trainer.train()
    trainer.save_model(cfg.output_dir)
    # Under DDP (torchrun --nproc_per_node=N) every rank reaches this point.
    # Trainer.save_model is already rank-0-guarded internally, but a bare
    # tokenizer.save_pretrained is not — N ranks writing the same files into one
    # directory concurrently can interleave and leave a truncated tokenizer.json.
    if int(os.environ.get("RANK", "0")) == 0:
        tokenizer.save_pretrained(cfg.output_dir)
        print(f"Saved adapter/tokenizer to: {cfg.output_dir}")


if __name__ == "__main__":
    main()
