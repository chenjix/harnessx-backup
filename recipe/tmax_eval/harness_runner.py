# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Run a Tmax task through the real HarnessX pipeline (prompt + processors + tools).

This replaces the thin OpenAI+Bash-only loop when a HarnessConfig YAML is
provided. Docker lifecycle stays in ``run_eval`` / ``docker_env``; this module
only drives the agent inside an already-started container.
"""
from __future__ import annotations

import asyncio
import json
import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harnessx.core.events import Message, ToolCall
from harnessx.core.harness import BaseTask, HarnessConfig
from harnessx.core.model_config import ModelConfig
from harnessx.providers.openai_provider import OpenAIProvider
from harnessx.tracing.journal import HarnessJournal

from .docker_sandbox import TmaxDockerSandboxProvider


@dataclass
class HarnessAgentResult:
    steps: int
    finished: str  # no_tool_calls | max_steps | error | interrupted | loop_detected | ...
    messages: list[dict[str, Any]]
    error: str | None = None
    exit_reason: str | None = None
    log: list[dict[str, Any]] = field(default_factory=list)


def _message_to_openai(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content if m.content is not None else ""}
    if m.tool_call_id:
        out["tool_call_id"] = m.tool_call_id
    if m.name:
        out["name"] = m.name
    if m.tool_calls:
        tcs = []
        for tc in m.tool_calls:
            if isinstance(tc, ToolCall):
                tcs.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.input, ensure_ascii=False),
                        },
                    }
                )
            elif isinstance(tc, dict):
                tcs.append(tc)
        if tcs:
            out["tool_calls"] = tcs
    return out


def _messages_from_state(state: Any) -> list[dict[str, Any]]:
    raw = getattr(state, "raw_messages", None) or getattr(state, "messages", None) or []
    out: list[dict[str, Any]] = []
    for m in raw:
        if isinstance(m, Message):
            out.append(_message_to_openai(m))
        elif isinstance(m, dict):
            out.append(m)
    return out


def _finished_from_result(result: Any) -> tuple[str, str | None, str | None]:
    """Map HarnessResult → (finished, exit_reason, error)."""
    exit_reason = getattr(result, "exit_reason", None)
    if exit_reason is None:
        task_end = getattr(result, "task_end", None)
        exit_reason = getattr(task_end, "exit_reason", None) if task_end is not None else None
    err = None
    if getattr(result, "is_interrupted", False):
        return "interrupted", exit_reason or "interrupted", None
    reason = str(exit_reason or "")
    if reason in ("loop_detected", "LoopDetectedError"):
        return "loop_detected", reason, None
    if reason in ("max_steps", "step_limit", "budget"):
        return "max_steps", reason, None
    if reason in ("completed", "success", "no_tool_calls", "done", ""):
        # Default successful stop when model stops calling tools.
        return "no_tool_calls", reason or "completed", None
    if reason in ("error", "failed"):
        return "error", reason, reason
    return (reason or "no_tool_calls"), reason or None, err


def _prefer_sibling_prompt_builder(config: HarnessConfig, cfg_path: Path) -> HarnessConfig:
    """If ``system_prompt.txt`` sits beside the YAML, make sure it is used.

    Older evolved configs still point ``SystemPromptProcessor`` at TB2's
    ``_StaticSystemPromptBuilder`` (inert under the old thin Tmax loop). With the
    full HarnessX path those would ignore the sidecar MetaAgent actually edited.
    Prefer ``SiblingSystemPromptBuilder`` whenever the sidecar exists, unless the
    YAML already wires that builder.
    """
    sibling = cfg_path.parent / "system_prompt.txt"
    if not sibling.is_file():
        return config
    new_procs: list[Any] = []
    changed = False
    sibling_target = "recipe.tmax_eval.prompt_builder.SiblingSystemPromptBuilder"
    for proc in config.processors or []:
        if not isinstance(proc, dict):
            new_procs.append(proc)
            continue
        target = str(proc.get("_target_", ""))
        if "system_prompt.SystemPromptProcessor" not in target:
            new_procs.append(proc)
            continue
        builder = proc.get("system_builder") or {}
        builder_target = ""
        if isinstance(builder, dict):
            builder_target = str(builder.get("_target_", ""))
        if sibling_target in builder_target:
            new_procs.append(proc)
            continue
        patched = dict(proc)
        patched["system_builder"] = {"_target_": sibling_target}
        new_procs.append(patched)
        changed = True
    if not changed:
        return config
    return config.copy(processors=new_procs)


async def _run_async(
    *,
    harness_config: Path,
    container: str,
    instruction: str,
    api_base: str,
    model: str,
    max_steps: int,
    temperature: float,
    api_key: str,
    journal_dir: Path | None,
    workspace_path: str = "/home/user",
) -> HarnessAgentResult:
    cfg_path = Path(harness_config).resolve()
    if not cfg_path.is_file():
        raise FileNotFoundError(f"harness config not found: {cfg_path}")

    # SiblingSystemPromptBuilder + promote paths resolve via this env.
    os.environ["TB2_HARNESS_CONFIG"] = str(cfg_path)
    os.environ["TMAX_HARNESS_CONFIG"] = str(cfg_path)
    sibling_prompt = cfg_path.parent / "system_prompt.txt"
    if sibling_prompt.is_file():
        os.environ["TMAX_SYSTEM_PROMPT_FILE"] = str(sibling_prompt)

    base_config = _prefer_sibling_prompt_builder(
        HarnessConfig.from_yaml_file(str(cfg_path)), cfg_path
    )
    tracer = None
    if journal_dir is not None:
        journal_dir.mkdir(parents=True, exist_ok=True)
        tracer = HarnessJournal(base_dir=str(journal_dir), export_jsonl=True)

    harness_config_rt = base_config.copy(
        sandbox_provider=TmaxDockerSandboxProvider(
            container,
            workspace_path=workspace_path,
        ),
        tracer=tracer,
    )

    provider_kwargs: dict[str, Any] = {
        "base_url": api_base.rstrip("/"),
        "api_key": api_key or "EMPTY",
    }
    if temperature is not None:
        provider_kwargs["temperature"] = float(temperature)
    # Cap per-call generation. Unbounded max_tokens lets a single degenerate
    # repetition loop emit 100k+ tokens and stall an evolve round for hours.
    # Override with TMAX_MAX_TOKENS (set 0 / negative to disable the cap).
    raw_max = (os.environ.get("TMAX_MAX_TOKENS") or "4096").strip()
    try:
        max_tokens = int(raw_max)
    except ValueError:
        max_tokens = 4096
    if max_tokens > 0:
        provider_kwargs["max_tokens"] = max_tokens
    provider = OpenAIProvider(model=model, **provider_kwargs)
    harness = ModelConfig(main=provider).agentic(harness_config_rt)
    task = BaseTask(description=instruction, max_steps=max_steps)

    try:
        result = await harness.run(task)
    except Exception as e:
        return HarnessAgentResult(
            steps=0,
            finished="error",
            messages=[],
            error=f"{type(e).__name__}: {e}",
            exit_reason="error",
            log=[{"traceback": traceback.format_exc()[-3000:]}],
        )

    state = getattr(result, "resume_state", None)
    messages = _messages_from_state(state) if state is not None else []
    finished, exit_reason, err = _finished_from_result(result)
    steps = 0
    if state is not None:
        steps = int(getattr(state, "step", 0) or 0)
    if steps <= 0:
        # Fall back to counting assistant turns.
        steps = sum(1 for m in messages if m.get("role") == "assistant")
    return HarnessAgentResult(
        steps=steps,
        finished=finished,
        messages=messages,
        error=err,
        exit_reason=exit_reason,
    )


def run_harness_agent(
    *,
    harness_config: Path,
    container: str,
    instruction: str,
    api_base: str,
    model: str,
    max_steps: int = 80,
    temperature: float = 0.0,
    api_key: str = "EMPTY",
    journal_dir: Path | None = None,
    workspace_path: str = "/home/user",
) -> HarnessAgentResult:
    """Sync wrapper for thread-pool workers in ``run_eval``."""
    return asyncio.run(
        _run_async(
            harness_config=harness_config,
            container=container,
            instruction=instruction,
            api_base=api_base,
            model=model,
            max_steps=max_steps,
            temperature=temperature,
            api_key=api_key,
            journal_dir=journal_dir,
            workspace_path=workspace_path,
        )
    )


__all__ = ["HarnessAgentResult", "run_harness_agent"]
