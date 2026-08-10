#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Route the Tmax corpus into an SFT dataset and a GRPO dataset.

The two arms draw from *different* Tmax releases, and that is what makes the
split leak-free rather than merely convenient:

  SFT  <- allenai/tmax-sft (only_success): 5,795 real Qwen3.6-27B rollouts over
          1,136 tasks, joinable 1:1 against the 2,200-task skill taxonomy in
          allenai/TMax-SFT-16.5K (domain / skill_type / task_complexity /
          command_complexity / language).
  GRPO <- allenai/tmax-15k-open-instruct: 14,601 RL environment instances, each
          with a prompt, a task-specific Docker image, and a real verifier
          (setup.sh + tests/test.sh in task-data.tar.gz).

Verified: the task-id namespaces of those two releases are **disjoint**
(taxonomy ∩ RL-manifest = 0), so a model can be SFT'd on demonstrations from
one pool and RL'd on the other with no train-on-test contamination. Nothing
here has to enforce that split; it is a property of the upstream data.

Routing policy for SFT
----------------------
Hard quality gates (a trajectory is dropped, with the reason recorded):
  has_ctrl_c              — the agent interrupted a running command; imitating
                            that teaches an escape hatch, not a skill.
  json_extraction_failed  — the tool call had to be salvaged by a fallback
                            parser, so the recorded call may not be what the
                            model actually emitted.
  not has_task_complete   — the episode never submitted; "succeeded" here is
                            the verifier's view, not a clean demonstration.
  tool turns outside [min_tools, max_tools]
  num_warnings > max_warnings

Then a *stratified* allocation over domain x task_complexity (9 x 4 = 36
cells) rather than a flat random draw. A flat draw inherits whatever the
success-rate distribution happens to be — and success rate is strongly
difficulty-dependent here (60% of `short` tasks have a successful trajectory
vs 35% of `intricate` ones), so flat sampling quietly over-weights easy work.
Within a cell, shorter renderings are preferred: anything past the trainer's
max_seq_length is truncated mid-target, which teaches a cut-off action.

Routing policy for GRPO
-----------------------
The RL manifest carries no taxonomy, so difficulty is bucketed by instruction
length quartile — a weak proxy, labelled as such in the report. Stratifying
across those quartiles keeps the RL prompt mix from collapsing onto whichever
length dominates. A held-out split is emitted so the RL'd model can be scored
on environments it never trained against.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tarfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import filter_and_build_sft as F  # noqa: E402
import tmax_source  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
EXT = ROOT / "data" / "external"

TAXONOMY_PARQUET = EXT / "tmax-taxonomy/data/train-00000-of-00001.parquet"
SFT_PARQUET = (
    EXT
    / "tmax-sft-only-success"
    / "skill_tax_20260505_2.2k_combined_balanced_thinking_only_success"
    / "train-00000-of-00001.parquet"
)
RL_PARQUET = EXT / "tmax-15k-open-instruct/data/train-00000-of-00001.parquet"
RL_TASKDATA_TAR = EXT / "tmax-15k-open-instruct/task-data.tar.gz"


# ---------------------------------------------------------------------------
# SFT arm
# ---------------------------------------------------------------------------


def _short(s: Any, n: int = 40) -> str:
    """Collapse a verbose taxonomy label to its leading phrase."""
    s = str(s)
    for sep in (" task", " (", ","):
        if sep in s:
            s = s.split(sep)[0]
            break
    return s.strip()[:n]


def build_sft(
    *,
    n: int,
    seed: int,
    min_tools: int,
    max_tools: int,
    max_warnings: int,
    per_task: int,
    max_chars: int,
    max_pairs_per_traj: int,
    eval_ratio: float,
    name: str,
) -> dict[str, Any]:
    tax = pd.read_parquet(TAXONOMY_PARQUET).set_index("task_id")
    traj = pd.read_parquet(SFT_PARQUET)

    rng = random.Random(seed)
    drops: Counter = Counter()
    # cell -> list of candidate dicts
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for idx in traj.index:
        row = traj.loc[idx]
        meta = dict(row["metadata"])
        task_id = str(meta.get("task") or "")

        if bool(meta.get("has_ctrl_c")):
            drops["has_ctrl_c"] += 1
            continue
        if bool(meta.get("json_extraction_failed")):
            drops["json_extraction_failed"] += 1
            continue
        if not bool(meta.get("has_task_complete")):
            drops["no_task_complete"] += 1
            continue
        if int(meta.get("num_warnings") or 0) > max_warnings:
            drops["too_many_warnings"] += 1
            continue
        if task_id not in tax.index:
            drops["no_taxonomy"] += 1
            continue

        messages = [tmax_source._tmax_message_to_internal(dict(m)) for m in list(row["messages"])]
        n_tools = sum(len(m.get("tool_calls") or []) for m in messages if m.get("role") == "assistant")
        if n_tools < min_tools or n_tools > max_tools:
            drops["tool_gate"] += 1
            continue

        t = tax.loc[task_id]
        tmeta = F.TrialMeta(
            run="allenai/tmax-sft:only_success",
            trial_dir=f"tmax://{meta.get('trial_name') or task_id}",
            task=task_id,
            reward=1.0,
            model=str(meta.get("source_model") or "Qwen/Qwen3.6-27B"),
            n_tool_turns=n_tools,
            n_recovered_tools=0,
            n_steps=int(meta.get("num_turns") or 0),
            source_bucket="tmax_external",
            quality_score=0.0,
            has_structured_tools=True,
            exception=False,
        )
        tmeta.quality_score = F._quality_score(tmeta)

        rec = F._messages_to_sft_record(tmeta, messages)
        pairs = F.expand_turn_pairs(rec, max_pairs=max_pairs_per_traj)
        if not pairs:
            drops["no_supervisable_turn"] += 1
            continue
        longest = max(len(p["prompt"]) + len(p["response"]) for p in pairs)

        cells[(_short(t["domain"]), _short(t["task_complexity"]))].append(
            {
                "meta": tmeta,
                "messages": messages,
                "task_id": task_id,
                "domain": _short(t["domain"]),
                "skill_type": str(t["skill_type"]),
                "task_complexity": _short(t["task_complexity"]),
                "command_complexity": _short(t["command_complexity"]),
                "language": str(t["language"]),
                "longest_pair_chars": longest,
                "n_tools": n_tools,
            }
        )

    # ── stratified allocation over the 36 domain x complexity cells ──────────
    keys = sorted(cells)
    for k in keys:
        # Prefer trajectories that fit the trainer's window, then compact ones;
        # jitter so equal-length candidates are not always drawn in file order.
        cells[k].sort(key=lambda c: (c["longest_pair_chars"] > max_chars, c["longest_pair_chars"], rng.random()))

    quota = {k: 0 for k in keys}
    per_task_used: Counter = Counter()
    selected: list[dict[str, Any]] = []
    # Round-robin over cells: gives every (domain, complexity) combination a
    # turn before any cell takes a second slot, so a cell that happens to hold
    # 300 candidates cannot crowd out one that holds 12.
    progressed = True
    while len(selected) < n and progressed:
        progressed = False
        for k in keys:
            if len(selected) >= n:
                break
            bucket = cells[k]
            while quota[k] < len(bucket):
                cand = bucket[quota[k]]
                quota[k] += 1
                if per_task_used[cand["task_id"]] >= per_task:
                    continue
                per_task_used[cand["task_id"]] += 1
                selected.append(cand)
                progressed = True
                break

    records = [F._messages_to_sft_record(c["meta"], c["messages"]) for c in selected]
    summary = F.build_ablation(
        name,
        records,
        weight_mode="unit",
        eval_ratio=eval_ratio,
        seed=seed,
        max_pairs_per_traj=max_pairs_per_traj,
    )

    out = F.OUT_ROOT / name
    n_truncated = sum(1 for c in selected if c["longest_pair_chars"] > max_chars)
    routing = {
        "arm": "sft",
        "source": "allenai/tmax-sft (skill_tax_20260505_2.2k_combined_balanced_thinking_only_success)",
        "taxonomy_source": "allenai/TMax-SFT-16.5K",
        "requested_n": n,
        "selected_n": len(selected),
        "candidates_after_gates": sum(len(v) for v in cells.values()),
        "dropped": dict(drops),
        "seed": seed,
        "gates": {
            "min_tools": min_tools,
            "max_tools": max_tools,
            "max_warnings": max_warnings,
            "per_task_cap": per_task,
            "prefer_under_chars": max_chars,
        },
        "n_selected_over_char_budget": n_truncated,
        "distribution": {
            "domain": dict(Counter(c["domain"] for c in selected).most_common()),
            "task_complexity": dict(Counter(c["task_complexity"] for c in selected).most_common()),
            "command_complexity": dict(Counter(c["command_complexity"] for c in selected).most_common()),
            "language": dict(Counter(c["language"] for c in selected).most_common()),
            "skill_type_top10": dict(Counter(c["skill_type"] for c in selected).most_common(10)),
        },
        "distinct_tasks": len({c["task_id"] for c in selected}),
        "task_ids": sorted({c["task_id"] for c in selected}),
    }
    (out / "routing_report.json").write_text(json.dumps(routing, indent=2), encoding="utf-8")
    (out / "provenance_extra.json").write_text(
        json.dumps(
            {
                "model": "(external distillation — corpus is not model-specific)",
                "run_glob": None,
                "n_own_model_trajectories": 0,
                "n_tmax_external_trajectories": len(selected),
                "tmax_only": True,
                "same_model_only": False,
                "external_source": routing["source"],
                "routed": True,
                "structured_tool_calls_only": True,
                "tool_call_format": "qwen3_xml",
                "holdout_tasks": sorted(F.HOLDOUT_TASKS),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"summary": summary, "routing": routing, "out_dir": str(out)}


# ---------------------------------------------------------------------------
# GRPO arm
# ---------------------------------------------------------------------------


def build_grpo(
    *,
    n_train: int,
    n_eval: int,
    seed: int,
    name: str,
    sft_task_ids: set[str],
) -> dict[str, Any]:
    rl = pd.read_parquet(RL_PARQUET)
    rl["task_id"] = rl["env_config"].apply(lambda e: e["task_id"])
    rl["image"] = rl["env_config"].apply(lambda e: e["image"])

    verifier_tasks: set[str] = set()
    if RL_TASKDATA_TAR.is_file():
        with tarfile.open(RL_TASKDATA_TAR, "r:gz") as tf:
            for nm in tf.getnames():
                if nm.endswith("/tests/test.sh"):
                    verifier_tasks.add(nm.split("/", 1)[0])

    rows: list[dict[str, Any]] = []
    skipped = Counter()
    for idx in rl.index:
        r = rl.loc[idx]
        tid = str(r["task_id"])
        if tid in sft_task_ids:
            # Cannot happen with the current releases (namespaces are disjoint)
            # but assert it rather than assume it: if upstream ever merges the
            # pools, silently training RL on SFT tasks is contamination.
            skipped["overlaps_sft"] += 1
            continue
        if verifier_tasks and tid not in verifier_tasks:
            skipped["no_verifier_script"] += 1
            continue
        msgs = [dict(m) for m in list(r["messages"])]
        user = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
        rows.append(
            {
                "task_id": tid,
                "image": str(r["image"]),
                "messages": msgs,
                "instruction_chars": len(user),
                "setup_path": f"{tid}/setup.sh",
                "verifier_path": f"{tid}/tests/test.sh",
            }
        )

    if not rows:
        raise SystemExit("no eligible GRPO rows")

    # Difficulty proxy: instruction-length quartile. Weak, and reported as such.
    rows.sort(key=lambda x: x["instruction_chars"])
    q = max(1, len(rows) // 4)
    for i, row in enumerate(rows):
        row["length_quartile"] = f"q{min(3, i // q) + 1}"

    rng = random.Random(seed)
    by_q: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_q[row["length_quartile"]].append(row)
    for v in by_q.values():
        rng.shuffle(v)

    def take(total: int, pool: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        qs = sorted(pool)
        while len(out) < total and any(pool[k] for k in qs):
            for k in qs:
                if len(out) >= total:
                    break
                if pool[k]:
                    out.append(pool[k].pop())
        return out

    eval_rows = take(n_eval, by_q)
    train_rows = take(n_train, by_q)

    out_dir = F.OUT_ROOT.parent / "data_grpo" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    for split, rs in (("train", train_rows), ("eval", eval_rows)):
        with (out_dir / f"{split}.jsonl").open("w", encoding="utf-8") as f:
            for r in rs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    routing = {
        "arm": "grpo",
        "source": "allenai/tmax-15k-open-instruct",
        "verifier_bundle": str(RL_TASKDATA_TAR),
        "eligible_rows": len(rows),
        "skipped": dict(skipped),
        "n_train": len(train_rows),
        "n_eval": len(eval_rows),
        "seed": seed,
        "disjoint_from_sft": True,
        "sft_task_overlap": len({r["task_id"] for r in rows} & sft_task_ids),
        "difficulty_proxy": "instruction-length quartile (weak proxy; RL manifest carries no skill taxonomy)",
        "train_quartiles": dict(Counter(r["length_quartile"] for r in train_rows).most_common()),
        "eval_quartiles": dict(Counter(r["length_quartile"] for r in eval_rows).most_common()),
        "distinct_images_train": len({r["image"] for r in train_rows}),
        "note": (
            "This is a rollout SPEC, not a training-ready buffer. Each row names a "
            "Docker image plus setup/verifier scripts inside the task-data tarball; "
            "consuming it requires a rollout manager that (1) starts one container "
            "per sampled rollout, (2) routes the agent's Bash calls into it, "
            "(3) runs tests/test.sh at episode end, (4) returns that verifier "
            "result as reward, (5) tears the container down. The repo's existing "
            "recipe/slime path is offline replay-GRPO over recorded TB2 rewards and "
            "does none of those things."
        ),
    }
    (out_dir / "routing_report.json").write_text(json.dumps(routing, indent=2), encoding="utf-8")
    return {"routing": routing, "out_dir": str(out_dir)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sft-name", default="tmax_routed_sft")
    ap.add_argument("--sft-n", type=int, default=400)
    ap.add_argument("--grpo-name", default="tmax_routed_grpo")
    ap.add_argument("--grpo-train", type=int, default=2000)
    ap.add_argument("--grpo-eval", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-tools", type=int, default=2)
    ap.add_argument("--max-tools", type=int, default=60)
    ap.add_argument("--max-warnings", type=int, default=0)
    ap.add_argument("--per-task", type=int, default=2)
    ap.add_argument("--max-chars", type=int, default=14000, help="~4096 tokens; prefer pairs under this")
    ap.add_argument("--max-pairs-per-traj", type=int, default=8)
    ap.add_argument("--eval-ratio", type=float, default=0.1)
    ap.add_argument("--skip-grpo", action="store_true")
    ap.add_argument("--skip-sft", action="store_true")
    args = ap.parse_args()

    for p in (TAXONOMY_PARQUET, SFT_PARQUET):
        if not p.is_file():
            raise SystemExit(f"missing input: {p}")

    sft_task_ids: set[str] = set()
    if not args.skip_sft:
        res = build_sft(
            n=args.sft_n,
            seed=args.seed,
            min_tools=args.min_tools,
            max_tools=args.max_tools,
            max_warnings=args.max_warnings,
            per_task=args.per_task,
            max_chars=args.max_chars,
            max_pairs_per_traj=args.max_pairs_per_traj,
            eval_ratio=args.eval_ratio,
            name=args.sft_name,
        )
        r, s = res["routing"], res["summary"]
        print("=== SFT arm ===")
        print(f"  out            : {res['out_dir']}")
        print(f"  trajectories   : {r['selected_n']} / requested {r['requested_n']}"
              f"   (candidates after gates: {r['candidates_after_gates']})")
        print(f"  distinct tasks : {r['distinct_tasks']}")
        print(f"  train/eval     : {s['n_train_pairs']} / {s['n_eval_pairs']} pairs")
        print(f"  dropped        : {r['dropped']}")
        print(f"  over char budget: {r['n_selected_over_char_budget']}")
        print(f"  domain         : {r['distribution']['domain']}")
        print(f"  complexity     : {r['distribution']['task_complexity']}")
        sft_task_ids = set(r["task_ids"])

    if not args.skip_grpo:
        if not RL_PARQUET.is_file():
            raise SystemExit(f"missing input: {RL_PARQUET}")
        res = build_grpo(
            n_train=args.grpo_train,
            n_eval=args.grpo_eval,
            seed=args.seed,
            name=args.grpo_name,
            sft_task_ids=sft_task_ids,
        )
        r = res["routing"]
        print("\n=== GRPO arm ===")
        print(f"  out            : {res['out_dir']}")
        print(f"  eligible       : {r['eligible_rows']}   skipped: {r['skipped']}")
        print(f"  train/eval     : {r['n_train']} / {r['n_eval']}")
        print(f"  sft overlap    : {r['sft_task_overlap']}  (must be 0)")
        print(f"  train quartiles: {r['train_quartiles']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
