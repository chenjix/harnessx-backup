#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Plan the SFT-gen top-up set for one coevolve iteration.

The SFT harvest plane is **not** the evolve-50 set. After harness evolve and
the holdout-H ratchet, we want ~``--target-size`` unique non-holdout tasks
rolled out under the *eval* harness (H*):

  reuse  = unique task ids already present in this iteration's evolve traj dirs
           (``{tag}-r*-traj``, including tournament ``fe-c*`` siblings)
  n_new  = max(0, target - |reuse|)
  fill   = domain-stratified draw of n_new tasks from the taxonomy pool,
           excluding holdout + the evolve set (the caller runs
           ``build_tmax_evolve_task_set``)

This module only counts reuse and writes a plan JSON. The loop script does
the fill + rollout so SFT-gen can share the same env-row pool helpers.

Usage::

  python -m recipe.tb2_sft.src.plan_tmax_sftgen \\
    --bench-root .benchmarks/tmax \\
    --run-tag tmax-coev-rep22-i1 \\
    --target-size 100 \\
    --out outputs/tmax_coevolve/rep22/sftgen_plan_i1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tmax_mastery import load_task_ids  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BENCH = _ROOT / ".benchmarks" / "tmax"


def unique_task_ids(*, bench_root: Path, run_tag: str) -> set[str]:
    """Task ids that already have a result under ``{run_tag}-r*`` dirs.

    Matches evolve rollouts (``{tag}-r0-traj``, ``{tag}-r0-fe-c3-traj``, …)
    and does **not** match a sibling SFT-gen dir named ``{tag}-sftgen-r0-traj``.
    """
    ids: set[str] = set()
    root = Path(bench_root)
    if not root.is_dir() or not run_tag:
        return ids
    for d in sorted(root.glob(f"{run_tag}-r*")):
        if not d.is_dir():
            continue
        # `{tag}-r0-traj` yes; `{tag}-sftgen-r0-traj` does not match `-r*`.
        summary = d / "summary.json"
        if summary.is_file():
            try:
                obj = json.loads(summary.read_text(encoding="utf-8"))
                for row in obj.get("results") or []:
                    tid = row.get("task_id") if isinstance(row, dict) else None
                    if tid:
                        ids.add(str(tid))
            except Exception:
                pass
        for p in d.glob("*.result.json"):
            if p.name == "result.json":
                continue
            ids.add(p.name[: -len(".result.json")])
    return ids


def plan(
    *,
    bench_root: Path,
    run_tag: str,
    target_size: int,
    evolve_tasks: Path | None = None,
) -> dict:
    reused = sorted(unique_task_ids(bench_root=bench_root, run_tag=run_tag))
    evolve_ids = load_task_ids(evolve_tasks) if evolve_tasks else []
    # Prefer the evolve-set list when present (covers tasks that failed to
    # write a result file — we still do not want to re-roll them as "new").
    reuse_set = set(reused) | set(evolve_ids)
    n_reuse = len(reuse_set)
    n_new = max(0, int(target_size) - n_reuse)
    return {
        "run_tag": run_tag,
        "target_size": int(target_size),
        "n_reuse": n_reuse,
        "n_new": n_new,
        "reused_from_results": reused,
        "evolve_task_ids": evolve_ids,
        "reuse_union": sorted(reuse_set),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench-root", type=Path, default=_DEFAULT_BENCH)
    ap.add_argument("--run-tag", required=True)
    ap.add_argument("--target-size", type=int, default=100)
    ap.add_argument(
        "--evolve-tasks",
        type=Path,
        default=None,
        help="This iteration's evolve-set JSON; unioned with result-file ids so "
        "failed evolve tasks are not redrawn as SFT-gen fill.",
    )
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    payload = plan(
        bench_root=args.bench_root,
        run_tag=args.run_tag,
        target_size=args.target_size,
        evolve_tasks=args.evolve_tasks,
    )
    text = json.dumps(payload, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(f"{payload['n_reuse']}\t{payload['n_new']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
