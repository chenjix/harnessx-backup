#!/usr/bin/env python3
"""Build an evolve set with a real capability gap from a screening run.

An evolve set the model already aces teaches the meta-agent nothing: with no
failing task there is no gap to close, so it ships no-ops and the campaign's
harness comes out byte-identical to its input. That is exactly what happened to
the rep92 smoke (evolve set 4/4, four rounds, zero config change).

But an evolve set of PURE failures is nearly as bad. Two mechanisms need tasks
the parent already solves:

  * the mini-eval probe screen detects REGRESSIONS by re-running parent-solved
    tasks — with none available it can only measure upside, and a candidate
    that breaks everything else sails through;
  * the gate compares pass rates, and a set scoring 0/N gives it no gradient
    until something finally flips.

So the set is deliberately mixed. Default 10 failed + 6 passed.

Usage
-----
    # after the screening job, look at what the model can and cannot do
    python scripts/tmax/pick_evolve_tasks.py \
        --results-dir .benchmarks/tmax/tmax-screen-evolve50 --report-only

    # then write the A/B task sets
    python scripts/tmax/pick_evolve_tasks.py \
        --results-dir .benchmarks/tmax/tmax-screen-evolve50 \
        --n-failed 10 --n-passed 6 --holdout-size 30
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recipe.tb2_evolver.tb2_trajspec import read_per_task_results  # noqa: E402

DEFAULT_HOLDOUT_POOL = ROOT / "recipe" / "tb2_evolver" / "tasks_tmax_only200_ids.json"
DEFAULT_OUT_DIR = ROOT / "recipe" / "tb2_evolver"


def _report(results: dict[str, bool]) -> tuple[list[str], list[str]]:
    passed = sorted(t for t, ok in results.items() if ok)
    failed = sorted(t for t, ok in results.items() if not ok)
    total = len(results)
    rate = (len(passed) / total) if total else 0.0
    print(f"  screened : {total} tasks")
    print(f"  passed   : {len(passed)} ({rate:.1%})")
    print(f"  failed   : {len(failed)} ({1 - rate:.1%})")
    if not failed:
        print("\n  WARNING: the base model solved EVERY screened task. There is no")
        print("  capability gap here — an evolve run on this pool cannot improve the")
        print("  harness because there is nothing for it to fix. Screen a harder pool")
        print("  (e.g. --tasks tasks_tmax_only200_ids.json) before running the A/B.")
    elif len(failed) < 4:
        print(f"\n  WARNING: only {len(failed)} failing task(s). The focus assignments and")
        print("  the gate will both be starved of signal; consider screening a larger pool.")
    return passed, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", type=Path, required=True,
                    help="screening job dir under .benchmarks/tmax/")
    ap.add_argument("--report-only", action="store_true",
                    help="print the pass/fail split and exit without writing task files")
    ap.add_argument("--n-failed", type=int, default=10,
                    help="failing tasks in the evolve set — the capability gap (default: 10)")
    ap.add_argument("--n-passed", type=int, default=6,
                    help="passing tasks in the evolve set — regression detection (default: 6)")
    ap.add_argument("--holdout-size", type=int, default=30,
                    help="tasks sampled from the holdout pool (default: 30; 0 to skip)")
    ap.add_argument("--holdout-pool", type=Path, default=DEFAULT_HOLDOUT_POOL)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--tag", default="ab", help="filename tag (default: ab)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--make-r0-dir", type=Path, default=None,
                    help="also write a trajectory dir holding ONLY the evolve-set "
                         "tasks, for --r0-dir. Both A/B arms can then share one R0 "
                         "rollout without inheriting the screening set's denominator.")
    args = ap.parse_args()

    if not args.results_dir.is_dir():
        print(f"ERROR: results dir not found: {args.results_dir}", file=sys.stderr)
        return 2

    results = read_per_task_results(args.results_dir)
    if not results:
        print(f"ERROR: no per-task results under {args.results_dir}", file=sys.stderr)
        return 2

    print(f"=== difficulty screen: {args.results_dir}")
    passed, failed = _report(results)
    if args.report_only:
        print("\n  failing tasks:")
        for t in failed:
            print(f"    {t}")
        return 0
    if not failed:
        print("\nERROR: refusing to write an evolve set with no failing task.", file=sys.stderr)
        return 3

    rng = random.Random(args.seed)

    # Deterministic sample, and report honestly when the pool cannot fill the ask
    # rather than silently returning a smaller set.
    n_fail = min(args.n_failed, len(failed))
    n_pass = min(args.n_passed, len(passed))
    if n_fail < args.n_failed:
        print(f"  NOTE: only {n_fail} failing tasks available (asked {args.n_failed})")
    if n_pass < args.n_passed:
        print(f"  NOTE: only {n_pass} passing tasks available (asked {args.n_passed})")

    evolve = sorted(rng.sample(failed, n_fail) + rng.sample(passed, n_pass))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    evolve_path = args.out_dir / f"tasks_{args.tag}_evolve{len(evolve)}_list.json"
    evolve_path.write_text(json.dumps(evolve, indent=2) + "\n")
    print(f"\n  evolve set -> {evolve_path}")
    print(f"    {n_fail} failing + {n_pass} passing = {len(evolve)} tasks")

    # A sidecar of which evolve tasks the baseline failed. The experiment's
    # headline number is "how many of THESE flip", and recomputing it later from
    # a screening dir that may have been cleaned up is how results get muddled.
    baseline_path = args.out_dir / f"tasks_{args.tag}_evolve_baseline.json"
    baseline_path.write_text(json.dumps(
        {t: bool(results[t]) for t in evolve}, indent=2, sort_keys=True) + "\n")
    print(f"  baseline   -> {baseline_path}")

    if args.holdout_size > 0:
        pool = json.loads(args.holdout_pool.read_text())
        if isinstance(pool, dict):
            pool = list(pool.get("task_ids", []))
        overlap = set(pool) & set(evolve)
        if overlap:
            # Measuring generalization on tasks that were optimized against is
            # not a holdout. Drop them rather than reporting a contaminated number.
            print(f"  NOTE: dropping {len(overlap)} holdout task(s) that are in the evolve set")
            pool = [t for t in pool if t not in overlap]
        n_hold = min(args.holdout_size, len(pool))
        holdout = sorted(rng.sample(sorted(pool), n_hold))
        holdout_path = args.out_dir / f"tasks_{args.tag}_holdout{len(holdout)}_list.json"
        holdout_path.write_text(json.dumps(holdout, indent=2) + "\n")
        print(f"  holdout    -> {holdout_path}  ({len(holdout)} tasks, disjoint from evolve)")

    if args.make_r0_dir is not None:
        _write_r0_subset(
            src=args.results_dir, dst=args.make_r0_dir,
            tasks=evolve, results=results,
        )

    return 0


def _write_r0_subset(*, src: Path, dst: Path, tasks: list[str], results: dict[str, bool]) -> None:
    """Copy just the evolve-set trajectories into a fresh dir, with a summary
    whose pass_rate covers exactly those tasks.

    The denominator is the whole point. `read_round_score` reads `pass_rate`
    straight out of summary.json, so handing the loop the screening dir as-is
    would score R0 over all 50 screened tasks while R1+ score over the 16-task
    evolve set — and the gate would then be comparing two different fractions,
    silently. Rebuilding the summary keeps every round on one denominator.
    """
    dst = dst.resolve()
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    missing: list[str] = []
    for t in tasks:
        td = src / t
        if not td.is_dir():
            missing.append(t)
            continue
        if (dst / t).exists():
            shutil.rmtree(dst / t)
        shutil.copytree(td, dst / t)
        for sidecar in (f"{t}.result.json", f"{t}.messages.json"):
            sp = src / sidecar
            if sp.is_file():
                shutil.copy2(sp, dst / sidecar)
        copied += 1

    n_passed = sum(1 for t in tasks if results.get(t))
    summary = {
        "job_name": dst.name,
        "n_tasks": len(tasks),
        "n_passed": n_passed,
        "pass_rate": (n_passed / len(tasks)) if tasks else 0.0,
        "note": (
            "Subset of "
            + str(src)
            + " restricted to the evolve set; pass_rate is over these tasks only."
        ),
    }
    (dst / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"  R0 subset  -> {dst}")
    print(f"    {copied}/{len(tasks)} task dirs copied, "
          f"pass_rate={summary['pass_rate']:.4f} ({n_passed}/{len(tasks)})")
    if missing:
        print(f"    WARNING: {len(missing)} task(s) had no trajectory dir and were "
              f"skipped: {', '.join(missing[:4])}")
        print("    Those tasks will have no R0 evidence for the meta-agent to read.")


if __name__ == "__main__":
    raise SystemExit(main())
