#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Build the per-iteration harness-evolve task set for the TB2.1 coevolve loop.

TB2 counterpart of ``scripts/tmax/build_iter_evolve_tasks.py``. Same rotation
rule, much simpler mechanics: TB2 tasks are bare ids that Harbor resolves to
containers, so there is no taxonomy parquet to stratify over and no envs JSONL
to emit -- just a fixed pool to draw from.

  exclude = holdout
          U every task that already contributed a correct trajectory to an SFT
            corpus in this replicate (cumulative across iterations)
  prefer  = last iteration's tasks that survive the exclusion, i.e. the ones the
            model still cannot solve -- keep grinding those
  fill    = sample the rest of the evolve pool up to --n-tasks

The pool is finite (~45 of the 89 TB2 tasks), unlike Tmax's thousands-strong
taxonomy, so refill is exhaustible by design. Once the unused remainder runs
out the set simply shrinks to whatever is still unsolved, which is the intended
end state -- the alternative would be re-admitting solved tasks and refilling
the SFT corpus with work the model has already mastered. ``--min-tasks`` guards
against the degenerate case where rotation empties the set entirely.

Emits, under ``recipe/tb2_sft/data/<name>/``:
  tasks_list_flat.json   # plain JSON array -> evolve/eval --tasks
  iter_selection.json    # provenance: solved set, carried, fresh, pool state

Example:
  python scripts/tb2/build_iter_evolve_tasks_tb21.py \\
    --name tb21_coev_rep1_i2_evolveset --n-tasks 25 \\
    --pool recipe/tb2_evolver/tasks_tb21_evolve_pool.json \\
    --holdout-tasks recipe/tb2_evolver/tasks_tb21_holdout.json \\
    --prev-tasks recipe/tb2_sft/data/tb21_coev_rep1_i1_evolveset/tasks_list_flat.json \\
    --corpus-glob 'recipe/tb2_sft/data/tb21_coev_rep1_i*/summary.json'
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_POOL = _ROOT / "recipe/tb2_evolver/tasks_tb21_evolve_pool.json"
_DEFAULT_HOLDOUT = _ROOT / "recipe/tb2_evolver/tasks_tb21_holdout.json"
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="Dataset dir name under --out-root")
    ap.add_argument("--out-root", type=Path, default=_DEFAULT_DATA)
    ap.add_argument("--n-tasks", type=int, default=25)
    ap.add_argument(
        "--min-tasks",
        type=int,
        default=5,
        help="Fail rather than evolve against a set this small (default 5)",
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pool", type=Path, default=_DEFAULT_POOL)
    ap.add_argument("--prev-tasks", type=Path, default=None,
                    help="Previous iteration's evolve set (unsolved ones are preferred)")
    ap.add_argument("--corpus-glob", action="append", default=[],
                    help="Glob for SFT corpus summary.json files (repeatable)")
    ap.add_argument("--holdout-tasks", type=Path, default=_DEFAULT_HOLDOUT)
    ap.add_argument("--extra-exclude", type=Path, default=None,
                    help="Additional task ids to exclude")
    args = ap.parse_args()

    pool = load_ids(args.pool)
    if not pool:
        raise SystemExit(f"ERROR: empty or missing evolve pool: {args.pool}")

    holdout = set(load_ids(args.holdout_tasks))
    solved, sources = solved_tasks(args.corpus_glob)
    extra = set(load_ids(args.extra_exclude))
    exclude = holdout | solved | extra

    # A holdout task inside the evolve pool would leak the metric into training.
    leak = sorted(set(pool) & holdout)
    if leak:
        raise SystemExit(
            f"ERROR: {len(leak)} holdout task(s) present in the evolve pool "
            f"({', '.join(leak[:5])}). Rebuild the split before continuing."
        )

    prev = load_ids(args.prev_tasks)
    # Still-unsolved tasks from last round stay at the front of the queue.
    prefer = [t for t in prev if t in set(pool) and t not in exclude]
    available = [t for t in pool if t not in exclude and t not in set(prefer)]

    print(f"building TB2.1 evolve set '{args.name}' (target {args.n_tasks} tasks)")
    print(f"  pool             : {len(pool)}")
    print(f"  holdout excluded : {len(holdout)}")
    print(f"  solved excluded  : {len(solved)}")
    for s in sources:
        print(f"      from {s}")
    if extra:
        print(f"  extra excluded   : {len(extra)}")
    print(f"  carried forward  : {len(prefer)} / {len(prev)} from previous set")
    print(f"  pool remaining   : {len(available)} unused and unsolved")

    chosen = list(prefer[: args.n_tasks])
    need = args.n_tasks - len(chosen)
    if need > 0:
        fresh = list(available)
        random.Random(args.seed).shuffle(fresh)
        chosen.extend(fresh[:need])
    chosen.sort()

    if len(chosen) < args.n_tasks:
        print(
            f"  NOTE: only {len(chosen)}/{args.n_tasks} tasks available — the pool is "
            f"exhausted, so this iteration evolves against a smaller set. Expected on "
            f"TB2.1, where the pool is finite.",
            file=sys.stderr,
        )
    if len(chosen) < args.min_tasks:
        raise SystemExit(
            f"ERROR: only {len(chosen)} task(s) left after exclusion (min {args.min_tasks}). "
            f"The model has solved essentially the whole pool; stop the chain or widen it."
        )

    out_dir = Path(args.out_root) / args.name
    out_dir.mkdir(parents=True, exist_ok=True)
    flat = out_dir / "tasks_list_flat.json"
    flat.write_text(json.dumps(chosen, indent=2) + "\n", encoding="utf-8")

    carried = [t for t in chosen if t in set(prefer)]
    (out_dir / "iter_selection.json").write_text(
        json.dumps(
            {
                "name": args.name,
                "benchmark": "tb2",
                "n_tasks": len(chosen),
                "task_ids": chosen,
                "n_carried_forward": len(carried),
                "carried_forward": carried,
                "n_fresh": len(chosen) - len(carried),
                "fresh": [t for t in chosen if t not in set(prefer)],
                "n_pool": len(pool),
                "n_pool_remaining_after": len(
                    [t for t in pool if t not in exclude and t not in set(chosen)]
                ),
                "n_excluded_holdout": len(holdout),
                "n_excluded_solved": len(solved),
                "excluded_solved": sorted(solved),
                "corpus_sources": sources,
                "seed": args.seed,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"  wrote {len(chosen)} tasks -> {flat}")
    print(f"  carried={len(carried)} fresh={len(chosen) - len(carried)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
