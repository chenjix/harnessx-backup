#!/usr/bin/env python3
"""Build offline replay prompts from completed TB2 HarnessX sessions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def reward_of(result: dict) -> float | None:
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    reward = rewards.get("reward")
    return float(reward) if isinstance(reward, (int, float)) else None


def task_text(trial_dir: Path) -> str:
    candidates = sorted((trial_dir / "agent" / "oh_runs").rglob("*.jsonl"))
    candidates = [p for p in candidates if "_trace" not in p.name and "_state" not in p.name]
    for path in candidates:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "session_start" and str(event.get("task") or "").strip():
                return str(event["task"]).strip()
            if event.get("type") == "raw_user":
                message = event.get("message") or {}
                content = str(message.get("content") or "").strip()
                if content:
                    return content
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-root", required=True)
    ap.add_argument("--run-glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--success-only", action="store_true")
    args = ap.parse_args()

    bench_root = Path(args.bench_root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for run_dir in sorted(bench_root.glob(args.run_glob)):
        if not run_dir.is_dir():
            continue
        for trial_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            result_path = trial_dir / "result.json"
            if not result_path.is_file():
                continue
            result = json.loads(result_path.read_text())
            reward = reward_of(result)
            if args.success_only and not (reward is not None and reward > 0):
                continue
            prompt = task_text(trial_dir)
            if not prompt:
                continue
            rows.append(
                {
                    "prompt": prompt,
                    "label": "",
                    "metadata": {
                        "task_type": "tb2_replay",
                        "task_name": result.get("task_name") or trial_dir.name.split("__")[0],
                        "source_run": run_dir.name,
                        "harbor_reward": reward,
                    },
                }
            )
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} replay prompts to {out}")
    if not rows:
        raise SystemExit("No replay prompts found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
