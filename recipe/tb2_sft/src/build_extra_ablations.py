#!/usr/bin/env python3
"""Add two SFT ablations that use the trajectory data available today.

The stock builder gates on `min_tools=2`, which drops 13 of the 64 passing
trajectories, and it never sees the Qwen-native runs as a distinct group.

  A5_all_success_min1  — every passing trajectory (min_tools=1), holdout excluded
  A6_qwen_native       — only trajectories produced BY Qwen3.5 models, so SFT is
                         self-distillation rather than gemma-31B -> 4B transfer

Run from recipe/tb2_sft_ablation.
"""
import sys
import json

sys.path.insert(0, "src")
import filter_and_build_sft as F  # noqa: E402

EVAL_RATIO = 0.08
SEED = 42
MAX_PAIRS = 6

QWEN_MODEL_HINTS = ("qwen3.5", "qwen/qwen3.5")


def to_recs(items):
    # `to_recs` is a closure inside the stock builder's main(); mirror it here.
    return [F._messages_to_sft_record(m, msgs) for m, msgs in items]


def main() -> int:
    # min_tools=1 keeps short-but-valid solutions (e.g. one decisive command).
    successes, recoveries = F.scan_trials(1, 60)
    print(f"scanned successes={len(successes)} recoveries={len(recoveries)}")

    train_success = [(m, msgs) for m, msgs in successes if m.task not in F.HOLDOUT_TASKS]
    print(f"after holdout filter: {len(train_success)}")

    qwen_native = [
        (m, msgs) for m, msgs in train_success
        if any(h in (m.model or "").lower() for h in QWEN_MODEL_HINTS)
    ]
    print(f"qwen-native subset: {len(qwen_native)}")
    for m, _ in qwen_native:
        print(f"   {m.run:52s} {m.task:32s} {m.model}")

    summaries = []
    summaries.append(F.build_ablation(
        "A5_all_success_min1", to_recs(train_success),
        EVAL_RATIO, SEED + 10, MAX_PAIRS,
    ))
    if qwen_native:
        summaries.append(F.build_ablation(
            "A6_qwen_native", to_recs(qwen_native),
            EVAL_RATIO, SEED + 11, MAX_PAIRS,
        ))
    else:
        print("WARNING: no qwen-native trajectories, skipping A6")

    for s in summaries:
        print(f"\n== {s['name']} ==")
        print(f"   trajectories={s['n_trajectories']} train={s['n_train_pairs']} eval={s['n_eval_pairs']}")
        print(f"   tasks({len(s['tasks'])})={s['tasks']}")
        print(f"   runs={s['runs']}")

    out = F.OUT_ROOT / "extra_ablations_report.json"
    out.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
