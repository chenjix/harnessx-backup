#!/usr/bin/env python3
"""Answer one question about an RL run: did it actually learn anything?

Exit status is not that answer. Job 32060 printed "trainer exit status: 0",
saved a final checkpoint, and had produced exactly zero gradient — every step
reported grad_norm 0.00e+00 because every rollout scored 0, so GRPO's
group-relative advantage was 0 everywhere. The weights that got saved were the
weights it started with.

The signal to look for is REWARD VARIANCE, not reward height. GRPO's advantage
is a rollout's reward minus its group's mean, so a prompt whose samples all
score the same contributes nothing — and that is true whether they all fail
(0,0,0,0) or all succeed (1,1,1,1). A run only learns from prompts that land in
between. This is what `--filter-zero-std` is filtering, and a run where it
filters everything is a run that is burning GPUs to multiply by zero.

    python scripts/tmax/rl_learning_verdict.py logs/tmax_rl_base9b_1234.out
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# The trainer prints a rich-format panel per step; strip the box drawing and
# read `name: value` pairs out of the flattened text.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BOX = re.compile(r"[│╭╮╰╯─]")
_PANEL = re.compile(r"Metrics")
_PAIR = re.compile(r"([A-Za-z][\w/]*(?:_[\w/]+)*)\s*:\s*(-?\d+\.?\d*(?:[eE][+-]?\d+)?)")

WATCHED = (
    "training_step",
    "percent_solved_mean",
    "verifiable_correct_rate",
    "verifiable_reward",
    "grad_norm",
    "policy_avg",
    "total_avg",
    "filtered_prompts_zero",
    "filtered_prompts_solved",
    "total_prompts",
    "bash/avg_calls_per_rollout",
    "bash/failure_rate",
)


def parse_steps(text: str) -> list[dict[str, float]]:
    text = _ANSI.sub("", text)
    chunks = _PANEL.split(text)[1:]
    steps: list[dict[str, float]] = []
    for chunk in chunks:
        flat = _BOX.sub(" ", chunk)
        # Stop at the next panel's header so metrics cannot bleed across steps.
        got: dict[str, float] = {}
        for key, val in _PAIR.findall(flat):
            if key in WATCHED and key not in got:
                try:
                    got[key] = float(val)
                except ValueError:
                    continue
        if "training_step" in got:
            steps.append(got)
    return steps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path, help="trainer stdout/log to read")
    ap.add_argument("--min-steps", type=int, default=3,
                    help="steps required before a verdict is meaningful (default: 3)")
    args = ap.parse_args()

    if not args.log.is_file():
        print(f"ERROR: no such log: {args.log}", file=sys.stderr)
        return 2

    steps = parse_steps(args.log.read_text(errors="replace"))
    if not steps:
        print("No training-step metrics found. The trainer did not reach a step —")
        print("look for an exception above the first Metrics panel.")
        return 3

    print(f"=== {len(steps)} training step(s) parsed from {args.log.name}\n")
    hdr = f"{'step':>5}  {'solved':>7}  {'reward':>7}  {'grad_norm':>11}  {'policy_loss':>12}  {'calls':>6}"
    print(hdr)
    print("-" * len(hdr))
    for s in steps:
        print(
            f"{int(s.get('training_step', 0)):>5}  "
            f"{s.get('percent_solved_mean', float('nan')):>7.3f}  "
            f"{s.get('verifiable_reward', float('nan')):>7.3f}  "
            f"{s.get('grad_norm', float('nan')):>11.3e}  "
            f"{s.get('policy_avg', float('nan')):>12.3e}  "
            f"{s.get('bash/avg_calls_per_rollout', float('nan')):>6.2f}"
        )

    grads = [s.get("grad_norm", 0.0) for s in steps]
    solved = [s.get("percent_solved_mean", 0.0) for s in steps]
    calls = [s.get("bash/avg_calls_per_rollout", 0.0) for s in steps]
    nonzero_grad = [g for g in grads if g > 0]

    print("\n=== verdict")
    if len(steps) < args.min_steps:
        print(f"  INCONCLUSIVE — only {len(steps)} step(s); need >= {args.min_steps}.")
        return 1

    if not nonzero_grad:
        print("  NO LEARNING. grad_norm was 0 at every step: no parameter was updated,")
        print("  and the saved checkpoint is numerically the model it started from.")
        if max(solved, default=0.0) <= 0.0:
            if max(calls, default=0.0) <= 0.0:
                print("\n  Cause: the agent never called a tool. That is the documented")
                print("  symptom of a tool-parser mismatch — check RL_TOOL_PARSER against")
                print("  the model family before changing anything else.")
            else:
                print(f"\n  Cause: the agent acted ({max(calls):.1f} calls/rollout at best) but")
                print("  solved nothing, so every rollout scored 0 and GRPO's advantage was 0")
                print("  everywhere. Scaling episodes will not help — zero times more is zero.")
                print("  The task set has to contain problems this model sometimes solves.")
        else:
            print(f"\n  Odd: solve rate reached {max(solved):.3f} but no gradient followed.")
            print("  Check whether every prompt was dropped by --filter-zero-std, which")
            print("  removes groups whose samples all scored alike — all-solved included.")
        return 1

    frac = len(nonzero_grad) / len(grads)
    print(f"  LEARNING SIGNAL PRESENT. grad_norm > 0 on {len(nonzero_grad)}/{len(grads)} "
          f"steps ({frac:.0%}).")
    print(f"  solve rate: min {min(solved):.3f}  max {max(solved):.3f}")
    print(f"  grad_norm : min {min(nonzero_grad):.3e}  max {max(nonzero_grad):.3e}")
    if frac < 0.5:
        print("\n  But over half the steps still produced no gradient — the task set is")
        print("  mostly outside the band where this model both succeeds and fails.")
    if max(solved) >= 0.99:
        print("\n  Note: solve rate hit ~1.0. Groups that all succeed carry zero advantage")
        print("  just as all-fail groups do; if this persists the signal will die out.")
    print("\n  This says gradients flowed, not that the policy improved. Compare the")
    print("  checkpoint against the base model on a held-out set for that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
