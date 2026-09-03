#!/usr/bin/env python3
"""Tests for evolve-outcome RL task selection."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import select_rl_tasks_from_evolve as S  # noqa: E402


def _outcome(attempts: int, successes: int) -> dict:
    return {"attempts": attempts, "successes": successes, "errors": 0, "runs": []}


def test_bands() -> None:
    assert S.band_task(1, 0, min_runs=2) == S.BAND_SPARSE
    assert S.band_task(3, 0, min_runs=2) == S.BAND_ALL_FAIL
    assert S.band_task(3, 3, min_runs=2) == S.BAND_ALL_PASS
    assert S.band_task(4, 2, min_runs=2) == S.BAND_SPLIT


def test_select_prefers_split_drops_unanimous_and_mastered() -> None:
    outcomes = {
        "task_split": _outcome(4, 2),
        "task_fail": _outcome(5, 0),
        "task_pass": _outcome(5, 5),
        "task_mastered_split": _outcome(4, 1),
        "task_holdout": _outcome(4, 2),
        "task_sparse": _outcome(1, 0),
    }
    report = S.select_tasks(
        outcomes,
        n_tasks=10,
        min_runs=2,
        holdout={"task_holdout"},
        mastered={"task_mastered_split"},
        drop_unanimous=True,
        drop_mastered=True,
    )
    assert report["selected"] == ["task_split", "task_sparse"]
    assert "task_fail" in report["dropped_unanimous_fail"]
    assert "task_pass" in report["dropped_unanimous_pass"]
    assert "task_mastered_split" in report["dropped_mastered"]
    assert "task_holdout" in report["dropped_holdout"]


def test_rank_closer_to_half_first() -> None:
    outcomes = {
        "task_almost": _outcome(10, 1),
        "task_half": _outcome(10, 5),
    }
    report = S.select_tasks(
        outcomes,
        n_tasks=2,
        min_runs=2,
        holdout=set(),
        mastered=set(),
        drop_unanimous=True,
        drop_mastered=True,
    )
    assert report["selected"][0] == "task_half"


if __name__ == "__main__":
    test_bands()
    test_select_prefers_split_drops_unanimous_and_mastered()
    test_rank_closer_to_half_first()
    print("ok")
