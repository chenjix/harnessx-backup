#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Build the per-iteration harness-evolve task set for the Tmax coevolve loop.

The loop used to evolve against one frozen 50-task list forever, so every
iteration kept re-solving the same tasks the model had already mastered and the
SFT corpus kept absorbing near-duplicate trajectories. This rotates the set:

  exclude = holdout-102
          U every task that has already contributed a correct trajectory to an
            SFT corpus in this replicate (cumulative across all iterations)
  prefer  = last iteration's tasks that survive the exclusion, i.e. the ones the
            model still cannot solve -- keep grinding those
  fill    = stratified sample from the rest of the Tmax taxonomy up to --n-tasks

Emits, under ``recipe/tb2_sft/data/<name>/``:
  tasks_list_flat.json           # plain JSON array -> evolve --tasks
  eval_task_set_with_envs.jsonl  # env definitions  -> evolve --tmax-envs-jsonl
  tasks_list.json / train.jsonl / task_data/   (from the shared writer)
  iter_selection.json            # provenance: solved set, prefer, fill, counts

Example:
  python scripts/tmax/build_iter_evolve_tasks.py \\
    --name tmax_coev_rep1_i2_evolveset --n-tasks 50 \\
    --prev-tasks recipe/tb2_evolver/tasks_tmax_evolve50_list.json \\
    --corpus-glob 'recipe/tb2_sft/data/tmax_coev_rep1_i*/summary.json'
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SFT_SRC = _ROOT / "recipe" / "tb2_sft" / "src"
if str(_SFT_SRC) not in sys.path:
    sys.path.insert(0, str(_SFT_SRC))

import build_tmax_rl_dataset as B  # noqa: E402

_DEFAULT_HOLDOUT = _ROOT / "recipe/tb2_evolver/tasks_tmax_only200.json"
_DEFAULT_TAXONOMY = _ROOT / "data/external/tmax-taxonomy/data/train-00000-of-00001.parquet"
_DEFAULT_DATA = _ROOT / "recipe/tb2_sft/data"


def load_ids(path: Path | None) -> list[str]:
    """Read task ids from a plain array, or a dict keyed tasks/task_ids/task_names."""
    if path is None or not Path(path).is_file():
        return []
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw
    if isinstance(raw, dict):
        items = raw.get("tasks") or raw.get("task_ids") or raw.get("task_names") or []
    out: list[str] = []
    for x in items or []:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            tid = x.get("task_id") or x.get("name") or x.get("id") or x.get("task")
            if tid:
                out.append(str(tid))
    return out


def solved_tasks(corpus_globs: list[str]) -> tuple[set[str], list[str]]:
    """Union of `selected_tasks` over every SFT corpus summary that matches.

    `selected_tasks` is exactly the set of tasks whose correct trajectories were
    distilled into an SFT corpus -- the loop's own record of what the model has
    demonstrably learned to solve.
    """
    solved: set[str] = set()
    seen: list[str] = []
    for pattern in corpus_globs:
        for hit in sorted(glob.glob(pattern)):
            try:
                s = json.loads(Path(hit).read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                print(f"  WARN: unreadable corpus summary {hit}: {exc}", file=sys.stderr)
                continue
            tasks = s.get("selected_tasks") or []
            if tasks:
                solved.update(str(t) for t in tasks)
                seen.append(f"{hit} (+{len(set(tasks))})")
    return solved, seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="Dataset dir name under --out-root")
    ap.add_argument("--out-root", type=Path, default=_DEFAULT_DATA)
    ap.add_argument("--n-tasks", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--prev-tasks", type=Path, default=None,
                    help="Previous iteration's evolve task list (unsolved ones are preferred)")
    ap.add_argument("--corpus-glob", action="append", default=[],
                    help="Glob for SFT corpus summary.json files (repeatable)")
    ap.add_argument("--holdout-tasks", type=Path, default=_DEFAULT_HOLDOUT)
    ap.add_argument("--taxonomy-parquet", type=Path, default=_DEFAULT_TAXONOMY)
    ap.add_argument("--envs-jsonl", type=Path, default=None,
                    help="Draw from this env set instead of the taxonomy parquet")
    ap.add_argument("--extra-exclude", type=Path, default=None,
                    help="Additional task ids to exclude")
    args = ap.parse_args()

    holdout = set(load_ids(args.holdout_tasks))
    solved, sources = solved_tasks(args.corpus_glob)
    extra = set(load_ids(args.extra_exclude))
    exclude = holdout | solved | extra

    prev = load_ids(args.prev_tasks)
    # Keep the still-unsolved tasks from last round at the front of the queue.
    prefer = [t for t in prev if t not in exclude]

    print(f"building evolve set '{args.name}' (target {args.n_tasks} tasks)")
    print(f"  holdout excluded : {len(holdout)}")
    print(f"  solved excluded  : {len(solved)}")
    for s in sources:
        print(f"      from {s}")
    if extra:
        print(f"  extra excluded   : {len(extra)}")
    print(f"  carried forward  : {len(prefer)} / {len(prev)} from previous set")
    print(f"  to sample fresh  : {max(0, args.n_tasks - len(prefer))}")

    if args.envs_jsonl is not None:
        pool = B.load_from_envs_jsonl(args.envs_jsonl, exclude)
        by_id = {r["ground_truth"]: r for r in pool}
        chosen = [by_id[t] for t in prefer if t in by_id][: args.n_tasks]
        if len(chosen) < args.n_tasks:
            import random

            rest = [r for r in pool if r["ground_truth"] not in {c["ground_truth"] for c in chosen}]
            random.Random(args.seed).shuffle(rest)
            chosen.extend(rest[: args.n_tasks - len(chosen)])
        records = chosen
    else:
        records = B.select_from_taxonomy(
            taxonomy_parquet=args.taxonomy_parquet,
            exclude=exclude,
            prefer_ids=prefer,
            n_tasks=args.n_tasks,
            seed=args.seed,
        )

    if not records:
        raise SystemExit("ERROR: no tasks selected -- the exclusion set may have eaten the pool")
    if len(records) < args.n_tasks:
        print(
            f"  WARNING: only {len(records)}/{args.n_tasks} tasks available after exclusion",
            file=sys.stderr,
        )

    out_dir = Path(args.out_root) / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = B.write_dataset(records, out_dir)

    chosen_ids = [r["ground_truth"] for r in records]
    # evolve's --tasks wants a plain array (matches tasks_tmax_evolve50_list.json);
    # the shared writer's tasks_list.json is a dict, so emit both.
    flat = out_dir / "tasks_list_flat.json"
    flat.write_text(json.dumps(chosen_ids, indent=2) + "\n", encoding="utf-8")

    carried = [t for t in chosen_ids if t in set(prefer)]
    (out_dir / "iter_selection.json").write_text(
        json.dumps(
            {
                "name": args.name,
                "n_tasks": len(chosen_ids),
                "task_ids": chosen_ids,
                "n_carried_forward": len(carried),
                "carried_forward": carried,
                "n_fresh": len(chosen_ids) - len(carried),
                "fresh": [t for t in chosen_ids if t not in set(prefer)],
                "n_excluded_holdout": len(holdout),
                "n_excluded_solved": len(solved),
                "excluded_solved": sorted(solved),
                "n_extra_excluded": len(extra),
                "extra_exclude_path": str(args.extra_exclude) if args.extra_exclude else None,
                "corpus_sources": sources,
                "seed": args.seed,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"  wrote {len(chosen_ids)} tasks -> {flat}")
    print(f"  envs  -> {summary.get('envs_jsonl')}")
    print(f"  carried={len(carried)} fresh={len(chosen_ids) - len(carried)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
