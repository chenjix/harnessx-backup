#!/usr/bin/env python3
"""Combine three full TB2 evaluations per model into per-task pass@3."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


PREFIX = {
    "base": "base-qwen35-9b-tb21-",
    "sft": "rep22-best-tb21-native-",
    "rl": "rep46-rl2-rep19h1-tb21-native-",
}


def read_run(run_dir: Path) -> dict[str, bool]:
    outcomes: dict[str, bool] = {}
    for result_path in run_dir.glob("*/result.json"):
        with result_path.open() as fh:
            result = json.load(fh)
        task = result.get("task_name")
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        if not task:
            raise ValueError(f"missing task_name in {result_path}")
        if task in outcomes:
            raise ValueError(f"duplicate task {task!r} in {run_dir}")
        outcomes[task] = float(rewards.get("reward", 0.0) or 0.0) > 0.0
    if len(outcomes) != 89:
        raise ValueError(f"{run_dir}: expected 89 results, found {len(outcomes)}")
    return outcomes


def resolve_runs(root: Path, model: str, first_run: str, stamp: str) -> list[Path]:
    runs = [root / first_run]
    for trial in (2, 3):
        matches = sorted(root.glob(f"{PREFIX[model]}{stamp}-t{trial}"))
        if len(matches) != 1:
            raise ValueError(
                f"{model} trial {trial}: expected one directory matching "
                f"{PREFIX[model]}{stamp}-t{trial}, found {len(matches)}"
            )
        runs.append(matches[0])
    return runs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--bench-root", type=Path, default=Path(".benchmarks/tb2"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with args.manifest.open(newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    if len(rows) != 6:
        raise ValueError(f"expected 6 submitted jobs in {args.manifest}, found {len(rows)}")

    stamps = {row["eval_stamp"].rsplit("-t", 1)[0] for row in rows}
    if len(stamps) != 1:
        raise ValueError(f"manifest contains inconsistent stamps: {sorted(stamps)}")
    stamp = stamps.pop()
    first_runs = {row["model"]: row["first_run"] for row in rows}
    output = args.output or args.manifest.with_name(args.manifest.stem + "-results.tsv")

    detail_rows: list[tuple[str, str, int, int, int, bool]] = []
    print("model\tpass@1 trials\tpass@3\ttotal")
    for model in ("base", "sft", "rl"):
        run_dirs = resolve_runs(args.bench_root, model, first_runs[model], stamp)
        trials = [read_run(path) for path in run_dirs]
        task_sets = [set(trial) for trial in trials]
        if not task_sets[0] == task_sets[1] == task_sets[2]:
            raise ValueError(f"{model}: task sets differ across trials")
        pass1 = [sum(trial.values()) for trial in trials]
        pass3 = 0
        for task in sorted(task_sets[0]):
            values = [int(trial[task]) for trial in trials]
            passed = any(values)
            pass3 += int(passed)
            detail_rows.append((model, task, *values, passed))
        print(f"{model}\t{'/'.join(map(str, pass1))}\t{pass3}\t89")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(("model", "task", "trial1", "trial2", "trial3", "pass_at_3"))
        writer.writerows(detail_rows)
    print(f"Per-task results: {output}")


if __name__ == "__main__":
    main()
