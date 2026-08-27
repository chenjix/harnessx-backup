#!/usr/bin/env python3
"""Compare the fan-out A/B arms from their evolve state files.

Reports, per arm: the per-round score trace, the best config's mean score, and
which baseline-failing tasks it actually flipped. For the fan-out arm it also
reports screen attribution — how many candidates each screen killed — because a
screen that kills everything and a screen that kills nothing are both bugs, and
the aggregate score alone hides either.

    python scripts/tmax/compare_fanout_ab.py --exp-tag fanout-ab
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "recipe" / "tb2_evolver" / "runs"


def _state(exp_tag: str, arm: str) -> dict | None:
    p = RUNS / f"{exp_tag}-{arm}" / "_meta_v2" / "_meta_scratch" / "harness_evolve_state.json"
    if not p.is_file():
        print(f"  [{arm}] no state file at {p}")
        return None
    return json.loads(p.read_text())


def _mean(xs) -> float | None:
    xs = [float(x) for x in (xs or [])]
    return (sum(xs) / len(xs)) if xs else None


def _report_arm(arm: str, state: dict, baseline: dict[str, bool] | None) -> dict:
    print(f"\n── ARM {arm} " + "─" * 52)
    hist = state.get("history") or []
    per_round = state.get("per_round_task_results") or {}
    scores = state.get("config_scores") or {}

    print(f"  rounds run   : {len(hist)}")
    trace = []
    for r in sorted(per_round, key=lambda k: int(k)):
        res = per_round[r]
        n_ok = sum(1 for v in res.values() if v)
        trace.append(f"R{r}:{n_ok}/{len(res)}")
    print(f"  score trace  : {' → '.join(trace) if trace else '(none)'}")

    best = state.get("best_so_far") or {}
    if best:
        print(f"  best         : R{best.get('round')} score={best.get('score')}")

    # Distinct configs actually measured — the honest count of experiments run.
    print(f"  configs measured: {len(scores)}")
    for digest, vals in list(scores.items())[:8]:
        m = _mean(vals)
        print(f"    {digest}  n={len(vals)}  mean={m:.4f}" if m is not None else f"    {digest}  n=0")

    flipped: list[str] = []
    if baseline and per_round:
        last = per_round[max(per_round, key=lambda k: int(k))]
        flipped = sorted(t for t, was_ok in baseline.items() if not was_ok and last.get(t))
        broke = sorted(t for t, was_ok in baseline.items() if was_ok and last.get(t) is False)
        n_gap = sum(1 for v in baseline.values() if not v)
        print(f"  gap closed   : {len(flipped)}/{n_gap} baseline-failing tasks now pass")
        if flipped:
            print(f"    fixed  : {', '.join(flipped[:6])}{' …' if len(flipped) > 6 else ''}")
        if broke:
            print(f"    BROKE  : {', '.join(broke[:6])}{' …' if len(broke) > 6 else ''}")

    # Fan-out only: what the screens did.
    archive = state.get("archive") or {}
    if archive:
        by_status = Counter(n.get("status", "?") for n in archive.values())
        by_screen = Counter(
            (n.get("screen") or {}).get("dropped_by") or "-"
            for n in archive.values()
            if n.get("status") == "screened_out"
        )
        print(f"  archive      : {len(archive)} nodes  {dict(by_status)}")
        if by_screen:
            print(f"  screens killed: {dict(by_screen)}")
            total_props = sum(by_status.values()) - by_status.get("pending", 0)
            if by_status.get("screened_out", 0) == total_props and total_props:
                print("    WARNING: every proposal was screened out — the screens are "
                      "too strict, or the proposals are all no-ops.")
        elif len(archive) > 2:
            print("    NOTE: no candidate was ever screened out. If this is the fan-out "
                  "arm, the screens are not filtering anything.")

    return {
        "arm": arm,
        "rounds": len(hist),
        "best_score": best.get("score"),
        "flipped": len(flipped),
        "n_configs": len(scores),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp-tag", default="fanout-ab")
    ap.add_argument("--arms", default="AB")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="tasks_<tag>_evolve_baseline.json sidecar from pick_evolve_tasks.py")
    args = ap.parse_args()

    baseline = None
    if args.baseline and args.baseline.is_file():
        baseline = {k: bool(v) for k, v in json.loads(args.baseline.read_text()).items()}
        n_gap = sum(1 for v in baseline.values() if not v)
        print(f"baseline: {len(baseline)} evolve tasks, {n_gap} failing "
              f"({len(baseline) - n_gap} solved) — the gap to close")

    summaries = []
    for arm in [c for c in args.arms if c in "AB"]:
        st = _state(args.exp_tag, arm)
        if st is not None:
            summaries.append(_report_arm(arm, st, baseline))

    if len(summaries) == 2:
        a, b = summaries
        print("\n── verdict " + "─" * 51)
        print(f"  configs measured : A={a['n_configs']}  B={b['n_configs']}")
        print(f"  gap closed       : A={a['flipped']}  B={b['flipped']}")
        print(f"  best score       : A={a['best_score']}  B={b['best_score']}")
        print("\n  Read this as a direction, not a result: one run per arm on a "
              "~15-task set\n  cannot separate a real effect from noise "
              "(sd ~3 tasks). Repeat with\n  several seeds before concluding "
              "anything about the method.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
