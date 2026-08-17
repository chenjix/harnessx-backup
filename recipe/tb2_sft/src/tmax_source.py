#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Load successful trajectories from the external Tmax SFT dataset and
convert them into the same ``(TrialMeta, messages)`` shape
``filter_and_build_sft.py`` uses for TB2's own trajectories, so they can be
merged into the same SFT ablation build with no changes to the existing
render/expand/dedupe code.

Source dataset: ``allenai/tmax-sft``, config
``skill_tax_20260505_2.2k_combined_balanced_thinking_only_success`` — 5,795
real agent rollouts from Qwen/Qwen3.6-27B, pre-filtered by the dataset
publisher to successes only. Each row is one full trajectory: a `messages`
list (system/user/assistant/tool turns, assistant turns carrying
`tool_calls`), a `tools` schema, and `metadata` (task id, turn count,
source model, ...).

Two things this module has to translate, not just pass through:

1. Tmax's tool schema exposes a single lowercase ``bash`` function; TB2's
   own trajectories and the served model's tool schema use ``Bash``. If left
   as-is, the SFT target would teach the model to call a tool name the
   actual serving stack doesn't expose — the same class of bug that zeroed
   TB2 scores before the 2026-07-26 parser fix (see reference/results/).
2. Tmax's `tool_calls[].function.arguments` is already a parsed dict
   (`{"command": "..."}`), while ``filter_and_build_sft.render_tool_call_xml``
   (reused unmodified here) expects the same JSON-string-or-dict duck typing
   TB2's own ``parse_messages`` produces — handled by re-serializing to a
   JSON string before handing off.

Tmax's task ids (``task_NNNNNN_<hash>``) live in a completely different
namespace from TB2's sample16 task names, so there is no overlap with
``filter_and_build_sft.HOLDOUT_TASKS`` and mixing this data in carries no
TB2-eval-contamination risk.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pandas as pd

import filter_and_build_sft as F


def _tmax_message_to_internal(m: dict[str, Any]) -> dict[str, Any]:
    """Convert one Tmax ``messages[i]`` entry into the internal message shape
    ``filter_and_build_sft`` already knows how to render (see
    ``parse_messages`` for the TB2-native equivalent of this conversion).
    """
    role = m.get("role")
    if role == "tool":
        tool_call_ids = m.get("tool_call_ids") or []
        return {
            "role": "tool",
            "tool_call_id": str(tool_call_ids[0]) if len(tool_call_ids) else "",
            "name": "Bash",
            "content": m.get("content") or "[empty tool result]",
        }
    if role == "assistant":
        out: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
        tool_calls = m.get("tool_calls")
        if tool_calls is not None and len(tool_calls):
            openai_tcs = []
            for i, tc in enumerate(tool_calls):
                tc = dict(tc)
                fn = dict(tc.get("function") or {})
                name = str(fn.get("name") or "bash")
                if name.lower() == "bash":
                    name = "Bash"  # match TB2's served tool name, not Tmax's
                args = fn.get("arguments")
                if isinstance(args, dict):
                    arg_str = json.dumps(args, ensure_ascii=False)
                else:
                    arg_str = str(args or {})
                openai_tcs.append(
                    {
                        "id": str(tc.get("id") or f"tmax_call_{i}"),
                        "type": "function",
                        "function": {"name": name, "arguments": arg_str},
                    }
                )
            out["tool_calls"] = openai_tcs
        return out
    # system / user pass through unchanged.
    return {"role": role, "content": m.get("content") or ""}


def load_tmax_trajectories(
    parquet_path: Path,
    n: int,
    seed: int = 42,
    min_tools: int = 2,
    max_tools: int = 60,
    per_task: int = 2,
    task_allowlist: set[str] | None = None,
) -> tuple[list[tuple[F.TrialMeta, list[dict[str, Any]]]], dict[str, Any]]:
    """Return up to *n* ``(TrialMeta, messages)`` tuples sampled from Tmax's
    only-success trajectories, plus a provenance dict for the build report.

    Sampling is shuffle-then-gate-then-dedupe(per_task)-then-take(n), so the
    result is a reproducible (seeded) near-random draw across Tmax's task
    pool, capped at *per_task* solutions per Tmax task id for diversity.

    When *task_allowlist* is set, only trajectories whose metadata task id is
    in that set are eligible (used to grow N while staying on the same eval
    task set, e.g. the 102 tasks from tmax_only200).
    """
    df = pd.read_parquet(parquet_path)
    rng = random.Random(seed)
    order = list(df.index)
    rng.shuffle(order)

    allow = {str(t) for t in task_allowlist} if task_allowlist else None
    candidates: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []
    skipped = {"tool_gate": 0, "no_tools": 0, "ctrl_c": 0, "not_in_allowlist": 0}
    for idx in order:
        row = df.loc[idx]
        meta_raw = dict(row["metadata"])
        task_id = str(meta_raw.get("task") or meta_raw.get("run_id") or idx)
        if allow is not None and task_id not in allow:
            skipped["not_in_allowlist"] += 1
            continue
        if bool(meta_raw.get("has_ctrl_c")):
            skipped["ctrl_c"] += 1
            continue

        messages = [_tmax_message_to_internal(dict(m)) for m in list(row["messages"])]
        n_tools = sum(len(m.get("tool_calls") or []) for m in messages if m.get("role") == "assistant")
        has_struct = n_tools > 0 and any(m.get("role") == "assistant" and m.get("tool_calls") for m in messages)
        if not has_struct:
            skipped["no_tools"] += 1
            continue
        if not (min_tools <= n_tools <= max_tools):
            skipped["tool_gate"] += 1
            continue

        trial_name = str(meta_raw.get("trial_name") or task_id)
        tmeta = F.TrialMeta(
            run="allenai/tmax-sft:only_success",
            trial_dir=f"tmax://{trial_name}",
            task=task_id,
            reward=1.0,
            model=str(meta_raw.get("source_model") or "Qwen/Qwen3.6-27B"),
            n_tool_turns=n_tools,
            n_recovered_tools=0,
            n_steps=int(meta_raw.get("num_turns") or 0),
            source_bucket="tmax_external",
            quality_score=0.0,
            has_structured_tools=True,
            exception=False,
        )
        tmeta.quality_score = F._quality_score(tmeta)
        candidates.append((tmeta, messages))

    # Spread across distinct Tmax tasks rather than letting one task's
    # multiple recorded solutions ("__sol1", "__sol2", ...) crowd out others.
    candidates = F.dedupe_by_task(candidates, per_task=per_task)

    # With an allowlist (sample-size ablation on a fixed eval task set), take a
    # round-robin across tasks so growing N does not accidentally drop tasks
    # that would have been covered by the smaller N draw.
    if allow is not None and candidates:
        by_task: dict[str, list[tuple[F.TrialMeta, list[dict[str, Any]]]]] = {}
        for item in candidates:
            by_task.setdefault(item[0].task, []).append(item)
        # Deterministic task order from seed (not alphabetical — keep diversity).
        task_order = list(by_task.keys())
        rng.shuffle(task_order)
        selected = []
        depth = 0
        while len(selected) < n:
            progressed = False
            for tid in task_order:
                bucket = by_task[tid]
                if depth < len(bucket):
                    selected.append(bucket[depth])
                    progressed = True
                    if len(selected) >= n:
                        break
            if not progressed:
                break
            depth += 1
    else:
        selected = candidates[:n]

    provenance = {
        "source": "allenai/tmax-sft (skill_tax_20260505_2.2k_combined_balanced_thinking_only_success)",
        "parquet": str(parquet_path),
        "requested_n": n,
        "selected_n": len(selected),
        "available_after_gates_and_dedupe": len(candidates),
        "skipped": skipped,
        "seed": seed,
        "per_task_cap": per_task,
        "task_allowlist_n": len(allow) if allow is not None else None,
        "selection": "round_robin_by_task" if allow is not None else "prefix_after_dedupe",
        "tasks": sorted({m.task for m, _ in selected}),
    }
    return selected, provenance


__all__ = ["load_tmax_trajectories"]
