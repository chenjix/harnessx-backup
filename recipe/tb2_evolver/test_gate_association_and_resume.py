#!/usr/bin/env python3
"""Regression tests for TB2 score-gate association + resume best restore."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile

RUN_PY = pathlib.Path(__file__).resolve().parent / "run.py"
spec = importlib.util.spec_from_file_location("tb2_run", RUN_PY)
mod = importlib.util.module_from_spec(spec)
sys.modules["tb2_run"] = mod
try:
    spec.loader.exec_module(mod)
except Exception as e:  # pragma: no cover - heavy deps
    print(f"note: full import failed ({type(e).__name__}), extracting helpers only")
    src = RUN_PY.read_text()
    start = src.index("def _score_and_gate_tb2(")
    end = src.index("async def main() -> None:")
    ns: dict = {}
    exec(compile(src[start:end], "gate", "exec"), ns)  # noqa: S102
    gate = ns["_score_and_gate_tb2"]
    best_from_state = ns["_best_from_state"]
    best_to_state = ns["_best_to_state_dict"]
else:
    gate = mod._score_and_gate_tb2
    best_from_state = mod._best_from_state
    best_to_state = mod._best_to_state_dict

TOL = 1.0 / 15.0


def test_gate_pins_to_evaluated_best() -> None:
    """Scores must be associated with the evaluated config, not a successor."""
    best = None
    active = "R0"
    # Observed 9B-style slide if R0 is strong then regressions accumulate.
    observed = [("R0", 8 / 15), ("R1", 4 / 15), ("R2", 5 / 15), ("R3", 5 / 15)]
    for idx, (label, score) in enumerate(observed):
        decision, reason, best, reverted = gate(
            round_idx=idx,
            round_score=score,
            round_config=label,
            best=best,
            tolerance=TOL,
        )
        if reverted is not None:
            active = str(reverted)
        else:
            active = label
        print(f"  {label} {score*15:.0f}/15 {decision} active={active} :: {reason}")
    assert active == "R0", f"expected pin to R0, got {active}"
    assert best is not None and best[0] == 8 / 15 and best[2] == 0


def test_resume_restores_best() -> None:
    with tempfile.TemporaryDirectory() as td:
        cfg = pathlib.Path(td) / "R0" / "config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("name: best\n")
        state = {
            "best_so_far": {"score": 0.5333, "config": str(cfg), "round": 0},
            "history": [],
        }
        restored = best_from_state(state)
        assert restored is not None
        assert restored[0] == 0.5333
        assert restored[1] == cfg.resolve()
        assert restored[2] == 0

        # Fallback from history when best_so_far missing.
        state2 = {
            "history": [
                {
                    "input_round": 0,
                    "gated_config": str(cfg),
                    "score": 0.5333,
                    "gate_decision": "accept",
                },
                {
                    "input_round": 1,
                    "gated_config": str(cfg),  # pretend same file exists
                    "score": 0.2667,
                    "gate_decision": "reject",
                },
            ]
        }
        restored2 = best_from_state(state2)
        assert restored2 is not None
        assert restored2[0] == 0.5333
        assert restored2[2] == 0

        # Round-trip helper.
        dumped = best_to_state(restored)
        assert dumped == {"score": 0.5333, "config": str(cfg.resolve()), "round": 0}


def test_resume_without_best_does_not_crash() -> None:
    assert best_from_state({}) is None
    assert best_from_state({"history": [{"score": 0.5}]}) is None


if __name__ == "__main__":
    print("=== evaluated-config gate association ===")
    test_gate_pins_to_evaluated_best()
    print("=== resume best restore ===")
    test_resume_restores_best()
    test_resume_without_best_does_not_crash()
    print("RESULT: PASS")
