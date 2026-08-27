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


def read_status_by_task(results_dir: Path) -> dict[str, str]:
    """Return ``{task: status}`` from the per-task result sidecars.

    read_per_task_results only looks at the verifier reward, so a task whose
    container failed to BUILD comes back as reward=0 -> False, byte-identical to
    a task the model genuinely could not solve. Selecting those as "the
    capability gap" would hand the meta-agent tasks that cannot run at all and
    ask it to evolve a harness fix for them.

    Job 33602 is the concrete case: a full root volume made 28 of 50 tasks fail
    with "docker build failed" in ~0.5s each, and nothing downstream could tell
    them apart from real failures.
    """
    out: dict[str, str] = {}
    for rp in sorted(results_dir.glob("*.result.json")):
        try:
            obj = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            continue
        task = str(obj.get("task_id") or obj.get("task_name") or rp.name[: -len(".result.json")])
        out[task] = str(obj.get("status") or "unknown")
    # The in-dir result.json carries status too, and is the authority when both
    # exist — the sidecar can be written before the trial finishes.
    for td in sorted(p for p in results_dir.iterdir() if p.is_dir()):
        rp = td / "result.json"
        if not rp.is_file():
            continue
        try:
            obj = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            continue
        task = str(obj.get("task_name") or obj.get("task_id") or td.name)
        out[task] = str(obj.get("status") or out.get(task, "unknown"))
    return out

DEFAULT_HOLDOUT_POOL = ROOT / "recipe" / "tb2_evolver" / "tasks_tmax_only200_ids.json"
DEFAULT_OUT_DIR = ROOT / "recipe" / "tb2_evolver"


def read_elapsed_by_task(results_dir: Path) -> dict[str, float]:
    """Return ``{task: elapsed_s}`` for tasks that produced a result."""
    out: dict[str, float] = {}
    for rp in sorted(results_dir.glob("*.result.json")):
        try:
            obj = json.loads(rp.read_text(encoding="utf-8"))
            out[str(obj.get("task_id") or rp.name[: -len(".result.json")])] = float(
                obj.get("elapsed_s") or 0.0
            )
        except Exception:
            continue
    return out


def _warn_on_duration_skew(
    passed: list[str], failed: list[str], elapsed: dict[str, float], factor: float = 2.0
) -> None:
    """Flag failures that took far longer than the passes.

    status alone does not catch a degraded environment. In job 33602 the node's
    disk filled mid-run; the tasks that ran afterwards still completed the agent
    loop and reported status=ok, but ground for 690-3048s against a full volume
    and scored 0 — while every genuine pass had finished in 85-428s. Selected as
    "the capability gap", those would have sent the meta-agent chasing harness
    fixes for an outage.

    This warns rather than excludes: a task that is genuinely hard can also be
    slow, and silently dropping the hardest tasks is its own way of faking a
    clean screen.
    """
    p_times = sorted(elapsed.get(t, 0.0) for t in passed if elapsed.get(t))
    f_times = [(elapsed.get(t, 0.0), t) for t in failed if elapsed.get(t)]
    if not p_times or not f_times:
        return
    p_max = p_times[-1]
    suspicious = sorted((v, t) for v, t in f_times if v > factor * p_max)
    if not suspicious:
        return
    print(f"\n  WARNING: {len(suspicious)} failing task(s) ran more than {factor:g}x longer than")
    print(f"  the slowest PASS ({p_max:.0f}s). A failure that takes far longer than any")
    print("  success is often the environment degrading, not the task being hard —")
    print("  check the node's disk/memory over the run before trusting these as gap:")
    for v, t in suspicious[:6]:
        print(f"    {t}  {v:.0f}s")
    if len(suspicious) > 6:
        print(f"    … and {len(suspicious) - 6} more")


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
    ap.add_argument("--slow-factor", type=float, default=2.0,
                    help="flag a failing task when it ran more than this multiple of the "
                         "slowest PASS (default: 2.0). A hint, not a verdict — it catches "
                         "the worst environmental casualties, not all of them.")
    ap.add_argument("--error-statuses", default="error,agent_error,timeout",
                    help="result statuses treated as infra failures and excluded from "
                         "BOTH pools (default: error,agent_error,timeout)")
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

    # Drop infra failures before anything reads the pass/fail split. An errored
    # task is not evidence about the model either way, so it belongs in neither
    # pool — not the gap, and not the regression detectors.
    status = read_status_by_task(args.results_dir)
    errored = sorted(t for t in results if status.get(t) in args.error_statuses.split(","))
    if errored:
        print(f"  EXCLUDED : {len(errored)} task(s) with infra errors "
              f"({args.error_statuses}) — not model failures, so not usable as a gap")
        for t in errored[:6]:
            print(f"    {t}")
        if len(errored) > 6:
            print(f"    … and {len(errored) - 6} more")
        results = {t: v for t, v in results.items() if t not in set(errored)}
        if not results:
            print("\nERROR: every screened task errored out. Fix the infrastructure "
                  "and re-screen; there is no difficulty signal here.", file=sys.stderr)
            return 3
        frac = len(errored) / (len(errored) + len(results))
        if frac > 0.2:
            print(f"\n  WARNING: {frac:.0%} of screened tasks errored. That is high enough "
                  f"that\n  the surviving sample may not represent the pool — consider "
                  f"re-screening\n  after fixing the cause rather than selecting from what is left.")

    passed, failed = _report(results)
    _warn_on_duration_skew(
        passed, failed, read_elapsed_by_task(args.results_dir), factor=args.slow_factor
    )
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
