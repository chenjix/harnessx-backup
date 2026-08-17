#!/usr/bin/env python3
"""Build a gemma-31B self-distillation SFT set.

Every trajectory here was produced BY google/gemma-4-31B-it during its own
evolve runs, so training gemma-31B on it is self-distillation rather than the
cross-model transfer the A1-A5 sets do (those are ~66% gemma-31B traces used to
train Qwen).

Run from recipe/tb2_sft_ablation.
"""
import sys
import json

sys.path.insert(0, "src")
import filter_and_build_sft as F  # noqa: E402

EVAL_RATIO = 0.08
SEED = 42
MAX_PAIRS = 6
NAME = "G1_gemma31b_self"


def to_recs(items):
    return [F._messages_to_sft_record(m, msgs) for m, msgs in items]


def main() -> int:
    successes, _ = F.scan_trials(1, 60)
    train = [(m, x) for m, x in successes if m.task not in F.HOLDOUT_TASKS]
    g31 = [(m, x) for m, x in train if "31b" in (m.model or "").lower()]
    print(f"gemma-31B self-produced trajectories: {len(g31)}")
    for m, _ in g31:
        print(f"   {m.run:56s} {m.task:32s} tools={m.n_tool_turns}")

    if not g31:
        print("no gemma-31B trajectories found")
        return 1

    s = F.build_ablation(NAME, to_recs(g31), EVAL_RATIO, SEED + 20, MAX_PAIRS)
    print(f"\n== {s['name']} ==")
    print(f"   trajectories={s['n_trajectories']} train={s['n_train_pairs']} eval={s['n_eval_pairs']}")
    print(f"   tasks({len(s['tasks'])})={s['tasks']}")
    print(f"   runs={s['runs']}")

    out = F.OUT_ROOT / "gemma31b_ablation_report.json"
    out.write_text(json.dumps([s], indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
