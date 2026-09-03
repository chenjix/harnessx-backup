#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Pick an RL train pool from tournament-evolve outcomes.

Online DPPO cannot ingest evolve conversations (no logprobs, wrong protocol).
What we *can* reuse is the pass/fail pattern across sibling harnesses:

  1. Drop unanimous-fail / unanimous-pass (zero-std groups in RL too).
  2. Rank remaining by disagreement (pass rate near 0.5, more sibling runs).
  3. Drop mastered tasks (already solved + harvested).

Usage::

  python -m recipe.tb2_sft.src.select_rl_tasks_from_evolve \\
    --replicate 22 --n-tasks 50 \\
    --out-dir recipe/tb2_sft/data/tmax_rl_rep22_split50
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from recipe.tb2_sft.src.tmax_mastery import (  # type: ignore
        _DEFAULT_BENCH,
        discover_traj_dirs,
        load_task_ids,
        scan_task_outcomes,
    )
except ImportError:  # running from this directory in unit tests
    from tmax_mastery import (  # type: ignore
        _DEFAULT_BENCH,
        discover_traj_dirs,
        load_task_ids,
        scan_task_outcomes,
    )

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_HOLDOUT = _ROOT / "recipe/tb2_evolver/tasks_tmax_only200.json"

BAND_SPLIT = "split"
BAND_SPARSE = "sparse"
BAND_ALL_FAIL = "unanimous_fail"
BAND_ALL_PASS = "unanimous_pass"


def _is_evolve_traj_dir(path: Path) -> bool:
    name = path.name
    if "-sftgen-" in name or "-B-harness" in name or "-E-" in name or "anchor" in name:
        return False
    return "-r" in name and name.endswith("-traj")


def discover_replicate_tags(bench_root: Path, replicate: int) -> list[str]:
    prefix = f"tmax-coev-rep{replicate}-i"
    tags: set[str] = set()
    for p in Path(bench_root).iterdir():
        if not p.is_dir() or not _is_evolve_traj_dir(p):
            continue
        name = p.name
        if not name.startswith(prefix):
            continue
        # tmax-coev-rep22-i3-r0-fe-c0-traj → tmax-coev-rep22-i3
        mid = name[len("tmax-coev-rep") :]
        # 22-i3-r0-...
        try:
            iter_part = mid.split("-r", 1)[0]  # 22-i3
            tags.add(f"tmax-coev-rep{iter_part}")
        except Exception:
            continue
    return sorted(tags, key=lambda t: (len(t), t))


def band_task(attempts: int, successes: int, min_runs: int) -> str:
    if attempts < min_runs:
        return BAND_SPARSE
    if successes <= 0:
        return BAND_ALL_FAIL
    if successes >= attempts:
        return BAND_ALL_PASS
    return BAND_SPLIT


def rank_key(rec: dict[str, Any]) -> tuple:
    """Lower is better: split first, then more runs, closer to 50% pass."""
    band = rec["band"]
    band_rank = {BAND_SPLIT: 0, BAND_SPARSE: 1, BAND_ALL_FAIL: 2, BAND_ALL_PASS: 3}[band]
    return (
        band_rank,
        -int(rec["attempts"]),
        abs(float(rec["pass_rate"]) - 0.5),
        rec["task_id"],
    )


def select_tasks(
    outcomes: dict[str, dict[str, Any]],
    *,
    n_tasks: int,
    min_runs: int,
    holdout: set[str],
    mastered: set[str],
    drop_unanimous: bool,
    drop_mastered: bool,
) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    for tid, raw in outcomes.items():
        if tid in holdout:
            continue
        attempts = int(raw.get("attempts") or 0)
        successes = int(raw.get("successes") or 0)
        rec = {
            "task_id": tid,
            "attempts": attempts,
            "successes": successes,
            "errors": int(raw.get("errors") or 0),
            "pass_rate": (successes / attempts) if attempts else 0.0,
            "band": band_task(attempts, successes, min_runs),
            "mastered": tid in mastered,
            "runs": [r.get("run") for r in (raw.get("runs") or [])],
        }
        scored.append(rec)

    dropped_holdout = sorted(tid for tid in outcomes if tid in holdout)
    dropped_mastered = sorted(r["task_id"] for r in scored if r["mastered"]) if drop_mastered else []
    dropped_unanimous_fail = sorted(
        r["task_id"] for r in scored if r["band"] == BAND_ALL_FAIL
    ) if drop_unanimous else []
    dropped_unanimous_pass = sorted(
        r["task_id"] for r in scored if r["band"] == BAND_ALL_PASS
    ) if drop_unanimous else []

    blocked = set(dropped_mastered) | set(dropped_unanimous_fail) | set(dropped_unanimous_pass)
    pool = [r for r in scored if r["task_id"] not in blocked]
    pool.sort(key=rank_key)
    selected = [r["task_id"] for r in pool[:n_tasks]]
    by_id = {r["task_id"]: r for r in scored}

    return {
        "n_scanned": len(outcomes),
        "n_selected": len(selected),
        "selected": selected,
        "dropped_holdout": dropped_holdout,
        "dropped_mastered": dropped_mastered,
        "dropped_unanimous_fail": dropped_unanimous_fail,
        "dropped_unanimous_pass": dropped_unanimous_pass,
        "n_split": sum(1 for r in scored if r["band"] == BAND_SPLIT and r["task_id"] not in blocked),
        "n_sparse": sum(1 for r in scored if r["band"] == BAND_SPARSE and r["task_id"] not in blocked),
        "tasks": by_id,
        "selected_bands": {tid: by_id[tid]["band"] for tid in selected},
    }


def write_outputs(report: dict[str, Any], out_dir: Path, extra: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {**extra, **report}
    (out_dir / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out_dir / "prefer_tasks.json").write_text(
        json.dumps(payload["selected"], indent=2) + "\n", encoding="utf-8"
    )
    exclude_extra = sorted(
        set(payload.get("dropped_mastered") or [])
        | set(payload.get("dropped_unanimous_fail") or [])
        | set(payload.get("dropped_unanimous_pass") or [])
    )
    (out_dir / "exclude_extra.json").write_text(
        json.dumps(exclude_extra, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {out_dir}: selected={payload['n_selected']} "
        f"split_pool={payload['n_split']} sparse_pool={payload['n_sparse']} "
        f"drop_fail={len(payload['dropped_unanimous_fail'])} "
        f"drop_pass={len(payload['dropped_unanimous_pass'])} "
        f"drop_mastered={len(payload['dropped_mastered'])}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replicate", type=int, default=None, help="Scan tmax-coev-repN-i*-r*-traj")
    ap.add_argument("--run-tag", action="append", default=[], help="Evolve run tag (repeatable)")
    ap.add_argument("--traj-dir", action="append", default=[], type=Path)
    ap.add_argument("--bench-root", type=Path, default=_DEFAULT_BENCH)
    ap.add_argument("--holdout", type=Path, default=_DEFAULT_HOLDOUT)
    ap.add_argument("--mastered", type=Path, default=None)
    ap.add_argument("--n-tasks", type=int, default=50)
    ap.add_argument("--min-runs", type=int, default=2, help="Attempts needed to call a task unanimous")
    ap.add_argument("--keep-unanimous", action="store_true", help="Do not drop all-0 / all-1 tasks")
    ap.add_argument("--keep-mastered", action="store_true")
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    run_tags = list(args.run_tag)
    if args.replicate is not None and not run_tags:
        run_tags = discover_replicate_tags(args.bench_root, args.replicate)
        if not run_tags:
            raise SystemExit(f"ERROR: no evolve traj dirs for replicate {args.replicate} under {args.bench_root}")

    traj_dirs = [
        p
        for p in discover_traj_dirs(
            bench_root=args.bench_root, run_tags=run_tags, traj_dirs=list(args.traj_dir)
        )
        if _is_evolve_traj_dir(p)
    ]
    if not traj_dirs:
        raise SystemExit("ERROR: no evolve trajectory dirs to scan")

    outcomes = scan_task_outcomes(traj_dirs)
    holdout = set(load_task_ids(args.holdout))
    mastered = set(load_task_ids(args.mastered))
    report = select_tasks(
        outcomes,
        n_tasks=args.n_tasks,
        min_runs=args.min_runs,
        holdout=holdout,
        mastered=mastered,
        drop_unanimous=not args.keep_unanimous,
        drop_mastered=not args.keep_mastered,
    )
    write_outputs(
        report,
        args.out_dir,
        extra={
            "replicate": args.replicate,
            "run_tags": run_tags,
            "n_traj_dirs": len(traj_dirs),
            "traj_dirs": [str(p) for p in traj_dirs],
            "min_runs": args.min_runs,
            "n_tasks_requested": args.n_tasks,
            "mastered_file": str(args.mastered) if args.mastered else None,
        },
    )
    if not report["selected"]:
        raise SystemExit("ERROR: evolve selector produced an empty RL pool")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
