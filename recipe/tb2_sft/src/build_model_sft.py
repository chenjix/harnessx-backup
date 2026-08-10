#!/usr/bin/env python3
"""Build same-model SFT pairs from quality-gated TB2 trajectories."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import filter_and_build_sft as F
import tmax_source

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / ".benchmarks" / "tb2"


def scan(run_glob: str, model: str, min_tools: int, max_tools: int):
    kept = []
    exclusions = []
    for run_dir in sorted(BENCH.glob(run_glob)):
        if not run_dir.is_dir():
            continue
        for trial_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            result_path = trial_dir / "result.json"
            obj = F._load_json(result_path) if result_path.is_file() else None
            if not obj:
                exclusions.append({"trial": str(trial_dir), "reason": "missing_or_invalid_result"})
                continue
            reward = F._reward_of(obj)
            task = str(obj.get("task_name") or trial_dir.name.split("__")[0])
            actual_model = F._model_of(obj)
            if actual_model != model:
                exclusions.append(
                    {"trial": str(trial_dir), "task": task, "reason": "wrong_model", "model": actual_model}
                )
                continue
            if reward is None or reward <= 0:
                exclusions.append({"trial": str(trial_dir), "task": task, "reason": "not_success", "reward": reward})
                continue
            if obj.get("exception_info") is not None:
                exclusions.append({"trial": str(trial_dir), "task": task, "reason": "exception"})
                continue
            session = F._find_session_jsonl(trial_dir)
            if session is None:
                exclusions.append({"trial": str(trial_dir), "task": task, "reason": "missing_session"})
                continue
            messages, stats = F.parse_messages(session)
            n_tools = int(stats.get("tool_calls", 0))
            has_structured = n_tools > 0 and any(
                m.get("role") == "assistant" and m.get("tool_calls") for m in messages
            )
            if not has_structured or not min_tools <= n_tools <= max_tools:
                exclusions.append(
                    {"trial": str(trial_dir), "task": task, "reason": "tool_gate", "n_tools": n_tools}
                )
                continue
            meta = F.TrialMeta(
                run=run_dir.name,
                trial_dir=str(trial_dir),
                task=task,
                reward=float(reward),
                model=model,
                n_tool_turns=n_tools,
                n_recovered_tools=int(stats.get("recovered_tools", 0)),
                n_steps=int(stats.get("assistant", 0)),
                source_bucket="evolve",
                quality_score=0.0,
                has_structured_tools=True,
                exception=False,
            )
            meta.quality_score = F._quality_score(meta)
            kept.append((meta, messages))
    return kept, exclusions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--run-glob", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--min-tools", type=int, default=2)
    ap.add_argument("--max-tools", type=int, default=60)
    ap.add_argument("--per-task", type=int, default=3)
    ap.add_argument("--max-pairs-per-traj", type=int, default=8)
    ap.add_argument("--eval-ratio", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--tmax-parquet",
        type=Path,
        default=None,
        help="Path to the Tmax only-success parquet to mix into this SFT corpus (see tmax_source.py).",
    )
    ap.add_argument(
        "--tmax-n",
        type=int,
        default=0,
        help="Number of external Tmax trajectories to mix in (0 = disabled, default).",
    )
    ap.add_argument("--tmax-seed", type=int, default=42)
    ap.add_argument(
        "--tmax-per-task",
        type=int,
        default=2,
        help="Max Tmax trajectories kept per Tmax task id, for diversity (default: 2).",
    )
    ap.add_argument(
        "--tmax-only",
        action="store_true",
        default=False,
        help=(
            "Build the corpus from Tmax trajectories ALONE — skip the own-model scan "
            "entirely. Use for a pure external-distillation arm, where mixing in a "
            "handful of own-model trajectories would muddy attribution."
        ),
    )
    args = ap.parse_args()

    if args.tmax_only and args.tmax_n <= 0:
        raise SystemExit("--tmax-only requires --tmax-n > 0")

    if args.tmax_only:
        successes, exclusions = [], []
        trainable, holdout = [], []
        print("tmax-only: skipping own-model trajectory scan")
    else:
        successes, exclusions = scan(args.run_glob, args.model, args.min_tools, args.max_tools)
        holdout = [(m, msgs) for m, msgs in successes if m.task in F.HOLDOUT_TASKS]
        trainable = [(m, msgs) for m, msgs in successes if m.task not in F.HOLDOUT_TASKS]
        trainable = F.dedupe_by_task(trainable, per_task=args.per_task)
    n_own = len(trainable)

    tmax_provenance: dict | None = None
    if args.tmax_n > 0:
        if args.tmax_parquet is None or not args.tmax_parquet.is_file():
            raise SystemExit(f"--tmax-n={args.tmax_n} requires --tmax-parquet pointing at an existing file")
        tmax_trainable, tmax_provenance = tmax_source.load_tmax_trajectories(
            args.tmax_parquet,
            n=args.tmax_n,
            seed=args.tmax_seed,
            min_tools=args.min_tools,
            max_tools=args.max_tools,
            per_task=args.tmax_per_task,
        )
        print(
            f"tmax_external: requested={args.tmax_n} selected={len(tmax_trainable)} "
            f"(available_after_gates_and_dedupe={tmax_provenance['available_after_gates_and_dedupe']}, "
            f"skipped={tmax_provenance['skipped']})"
        )
        trainable = trainable + tmax_trainable

    if not trainable:
        raise SystemExit(
            "No eligible trajectories from either source: "
            f"bench={BENCH} run_glob={args.run_glob!r} model={args.model!r} tmax_n={args.tmax_n}"
        )

    records = [F._messages_to_sft_record(m, msgs) for m, msgs in trainable]
    summary = F.build_ablation(
        args.name,
        records,
        weight_mode="unit",
        eval_ratio=args.eval_ratio,
        seed=args.seed,
        max_pairs_per_traj=args.max_pairs_per_traj,
    )
    out = F.OUT_ROOT / args.name
    (out / "exclusions.json").write_text(json.dumps(exclusions, indent=2) + "\n")
    if tmax_provenance is not None:
        (out / "tmax_provenance.json").write_text(json.dumps(tmax_provenance, indent=2) + "\n")
    (out / "provenance_extra.json").write_text(
        json.dumps(
            {
                "model": args.model,
                "run_glob": args.run_glob,
                "holdout_tasks": sorted(F.HOLDOUT_TASKS),
                "holdout_successes": [m.task for m, _ in holdout],
                "n_own_model_trajectories": n_own,
                "n_tmax_external_trajectories": len(trainable) - n_own,
                "tmax_only": bool(args.tmax_only),
                # Only claim same-model-only when no external data was mixed in —
                # a mixed corpus must say so loudly, not silently look "clean".
                "same_model_only": tmax_provenance is None,
                "external_source": tmax_provenance["source"] if tmax_provenance else None,
                "structured_tool_calls_only": True,
                "tool_call_format": "qwen3_xml",
            },
            indent=2,
        )
        + "\n"
    )

    bad = 0
    for line in (out / "train.jsonl").read_text().splitlines():
        response = json.loads(line)["response"]
        if "<tool_call>" in response and "<function=" not in response:
            bad += 1
    if bad:
        raise SystemExit(f"Refusing dataset: {bad} targets use non-qwen3_xml tool-call syntax")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
