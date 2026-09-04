#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Split the 89 TB2 tasks into a harness-evolve pool and a scoring holdout.

The Tmax coevolve chain draws its 50-task evolve set from a taxonomy of
thousands and scores on a disjoint 102-task holdout. TB2 has 89 tasks total, so
the same shape has to be cut out of one small, fixed universe. Two rules follow
from that:

  * the split is *stratified on the baseline outcome*. An unstratified 50/50 cut
    of 89 tasks can easily hand one side most of the solvable work; the holdout
    then measures the wrong thing and the evolve pool has no failures to learn
    from. Feeding in a baseline eval fixes both ends at once.
  * the evolve side is a **pool**, not the per-iteration set. The driver samples
    EVOLVE_N_TASKS (~25) out of it each iteration and refills from the unused
    remainder as solved tasks retire, which is how task rotation stays
    meaningful without a taxonomy to draw on. A pool the same size as the
    iteration set would make rotation a no-op.

Stratification key is (baseline passed?, task family), where the family is the
leading token of the task id -- `build-cython-ext` and `build-pmars` are both
`build`. Families are mostly singletons, so they only break ties; pass/fail does
the real work.

Emits, next to the task lists:
  tasks_tb21_evolve_pool.json  # plain array -> driver's evolve pool
  tasks_tb21_holdout.json      # plain array -> holdout eval + SFT exclusion
  tb21_split_provenance.json   # inputs, seed, per-stratum counts, baseline rates

Example:
  python scripts/tb2/split_tb21_tasks.py \\
    --baseline-job tb21-coev-rep1-scan89 \\
    --n-holdout 44 --seed 42
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_TASKS = _ROOT / "recipe" / "tb2_evolver" / "tasks_all_tb2.json"
_DEFAULT_BENCH = _ROOT / ".benchmarks" / "tb2"
_DEFAULT_OUT = _ROOT / "recipe" / "tb2_evolver"


def load_tasks(path: Path) -> list[str]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"ERROR: {path} must be a JSON array of task ids")
    out = [str(x) for x in raw if isinstance(x, str) and x.strip()]
    if not out:
        raise SystemExit(f"ERROR: {path} contained no task ids")
    return out


def baseline_outcomes(job_dir: Path) -> dict[str, bool]:
    """task -> passed, read from a TB2 harbor job dir.

    TB2 nests one dir per trial (``<task>__<trial>/result.json``) and records the
    verdict under ``verifier_result.rewards.reward``.
    """
    out: dict[str, bool] = {}
    if not job_dir.is_dir():
        return out
    for rp in sorted(job_dir.glob("*/result.json")):
        try:
            d = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            continue
        task = d.get("task_name") or rp.parent.name.split("__")[0]
        reward = ((d.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        passed = isinstance(reward, (int, float)) and reward >= 1.0
        # A task retried across shards counts as solved if any attempt solved it.
        out[str(task)] = out.get(str(task), False) or passed
    return out


def family(task: str) -> str:
    return task.split("-", 1)[0]


def split(
    tasks: list[str],
    outcomes: dict[str, bool],
    *,
    n_holdout: int,
    seed: int,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Deal each stratum alternately so both sides match on difficulty and family."""
    strata: dict[tuple[bool, str], list[str]] = defaultdict(list)
    for t in tasks:
        strata[(bool(outcomes.get(t, False)), family(t))].append(t)

    rng = random.Random(seed)
    holdout: list[str] = []
    evolve: list[str] = []
    # Largest strata first: the big groups set the balance, the singletons then
    # top up whichever side is short instead of dictating the split.
    for key in sorted(strata, key=lambda k: (-len(strata[k]), k[0], k[1])):
        members = sorted(strata[key])
        rng.shuffle(members)
        for t in members:
            # Fill toward the requested holdout share, then send the rest to the
            # pool. Comparing *fractions* keeps both sides on target even when
            # the strata are lopsided.
            want_holdout = len(holdout) * (len(tasks) - n_holdout) <= len(evolve) * n_holdout
            (holdout if want_holdout and len(holdout) < n_holdout else evolve).append(t)

    holdout.sort()
    evolve.sort()

    def rate(group: list[str]) -> float:
        if not group:
            return 0.0
        return sum(1 for t in group if outcomes.get(t)) / len(group)

    stats = {
        "n_total": len(tasks),
        "n_evolve_pool": len(evolve),
        "n_holdout": len(holdout),
        "baseline_known_for": len(outcomes),
        "baseline_pass_rate_all": rate(tasks),
        "baseline_pass_rate_evolve": rate(evolve),
        "baseline_pass_rate_holdout": rate(holdout),
        "n_unsolved_evolve": sum(1 for t in evolve if not outcomes.get(t)),
        "n_unsolved_holdout": sum(1 for t in holdout if not outcomes.get(t)),
        "n_strata": len(strata),
        "family_overlap": len(
            {family(t) for t in evolve} & {family(t) for t in holdout}
        ),
    }
    return evolve, holdout, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", type=Path, default=_DEFAULT_TASKS)
    ap.add_argument(
        "--baseline-job",
        default=None,
        help="TB2 job name under .benchmarks/tb2 holding the baseline eval of all tasks",
    )
    ap.add_argument("--bench-root", type=Path, default=_DEFAULT_BENCH)
    ap.add_argument("--n-holdout", type=int, default=44)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--prefix", default="tasks_tb21")
    ap.add_argument(
        "--allow-unstratified",
        action="store_true",
        help="Split without a baseline eval (difficulty balance is then luck)",
    )
    args = ap.parse_args()

    tasks = load_tasks(args.tasks)
    if not (0 < args.n_holdout < len(tasks)):
        raise SystemExit(f"ERROR: --n-holdout must be in 1..{len(tasks) - 1}")

    outcomes: dict[str, bool] = {}
    if args.baseline_job:
        job_dir = args.bench_root / args.baseline_job
        outcomes = baseline_outcomes(job_dir)
        if not outcomes:
            raise SystemExit(f"ERROR: no results under {job_dir}")
        missing = [t for t in tasks if t not in outcomes]
        if missing:
            print(
                f"WARNING: baseline covers {len(outcomes)}/{len(tasks)} tasks; "
                f"{len(missing)} treated as unsolved: {', '.join(missing[:8])}"
                f"{' …' if len(missing) > 8 else ''}",
                file=sys.stderr,
            )
    elif not args.allow_unstratified:
        raise SystemExit(
            "ERROR: pass --baseline-job so the split can be stratified on difficulty, "
            "or --allow-unstratified to accept an unbalanced split."
        )

    evolve, holdout, stats = split(
        tasks, outcomes, n_holdout=args.n_holdout, seed=args.seed
    )

    overlap = set(evolve) & set(holdout)
    if overlap:
        raise SystemExit(f"ERROR: split is not disjoint: {sorted(overlap)[:5]}")
    if len(evolve) + len(holdout) != len(tasks):
        raise SystemExit("ERROR: split lost or duplicated tasks")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    pool_path = args.out_dir / f"{args.prefix}_evolve_pool.json"
    hold_path = args.out_dir / f"{args.prefix}_holdout.json"
    prov_path = args.out_dir / "tb21_split_provenance.json"
    pool_path.write_text(json.dumps(evolve, indent=2) + "\n", encoding="utf-8")
    hold_path.write_text(json.dumps(holdout, indent=2) + "\n", encoding="utf-8")
    prov_path.write_text(
        json.dumps(
            {
                "tasks_source": str(args.tasks),
                "baseline_job": args.baseline_job,
                "seed": args.seed,
                "stats": stats,
                "evolve_pool": evolve,
                "holdout": holdout,
                "baseline_outcomes": {t: bool(outcomes.get(t, False)) for t in tasks},
                "family_counts_evolve": dict(Counter(family(t) for t in evolve)),
                "family_counts_holdout": dict(Counter(family(t) for t in holdout)),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"split {stats['n_total']} tasks (seed {args.seed})")
    print(f"  evolve pool : {stats['n_evolve_pool']:>3}  -> {pool_path}")
    print(f"  holdout     : {stats['n_holdout']:>3}  -> {hold_path}")
    if outcomes:
        print(
            f"  baseline    : all {stats['baseline_pass_rate_all']:.1%} | "
            f"evolve {stats['baseline_pass_rate_evolve']:.1%} | "
            f"holdout {stats['baseline_pass_rate_holdout']:.1%}"
        )
        print(
            f"  headroom    : {stats['n_unsolved_evolve']} unsolved in pool, "
            f"{stats['n_unsolved_holdout']} unsolved in holdout"
        )
        if stats["n_unsolved_evolve"] < 5:
            print(
                "  WARNING: almost nothing left for the harness to fix in the evolve "
                "pool; this chain will likely flatline.",
                file=sys.stderr,
            )
    print(f"  provenance  : {prov_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
