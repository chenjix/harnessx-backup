#!/usr/bin/env python3
"""Build a conservative Qwen3.5-9B SFT corpus from completed gated TB2 rounds.

The default output contains:
  * all quality-gated successful trajectories (reward=1), deduped per task;
  * a small number of conservative pairs from partially-correct failed runs.

A failed run is only eligible as "partial" when the same task succeeds in another
selected round.  From such a run we supervise only early assistant tool turns whose
immediate tool observation is non-error.  These pairs are deliberately capped so
the strict successful data dominates training.

The four benchmark holdout tasks remain excluded from train/eval and are exported
separately for analysis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

import filter_and_build_sft as F  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / ".benchmarks" / "tb2"
OUT_ROOT = Path(__file__).resolve().parents[1] / "data"
RUN_PREFIX = "tb2-q35-9b-gpt55-gated-r10-r"
MODEL = "Qwen/Qwen3.5-9B"

ERROR_OBS_RE = re.compile(
    r"(^|\n)\s*(?:error|exception|traceback|failed|failure|no such|not found|"
    r"command not found|syntax error|timed out|timeout|segmentation fault)\b",
    re.IGNORECASE,
)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _parse_rounds(raw: str) -> list[int]:
    rounds = sorted({int(x.strip()) for x in raw.split(",") if x.strip()})
    if not rounds or any(x < 0 for x in rounds):
        raise ValueError("--rounds must contain non-negative comma-separated integers")
    return rounds


def _scan_selected_runs(
    rounds: list[int],
    *,
    min_tools: int,
    max_tools: int,
) -> tuple[
    list[tuple[F.TrialMeta, list[dict[str, Any]]]],
    list[tuple[F.TrialMeta, list[dict[str, Any]]]],
    list[dict[str, Any]],
]:
    successes: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []
    failures: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []
    exclusions: list[dict[str, Any]] = []

    for round_idx in rounds:
        run = f"{RUN_PREFIX}{round_idx}-traj"
        run_dir = BENCH / run
        result_path = run_dir / "result.json"
        if not result_path.is_file():
            raise FileNotFoundError(f"missing aggregate result for selected run: {result_path}")
        aggregate = json.loads(result_path.read_text(encoding="utf-8"))
        if not aggregate.get("finished_at"):
            raise RuntimeError(f"selected run is still in progress: {run}")
        if int(aggregate.get("n_total_trials") or 0) != 15:
            raise RuntimeError(f"selected run is not the expected 15-task evaluation: {run}")

        for trial_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            rp = trial_dir / "result.json"
            if not rp.is_file():
                exclusions.append({"run": run, "trial_dir": str(trial_dir), "reason": "missing_result"})
                continue
            obj = F._load_json(rp)
            if not obj:
                exclusions.append({"run": run, "trial_dir": str(trial_dir), "reason": "invalid_result"})
                continue

            reward = F._reward_of(obj)
            model = F._model_of(obj)
            task = str(obj.get("task_name") or trial_dir.name.split("__")[0])
            if model != MODEL:
                exclusions.append(
                    {
                        "run": run,
                        "trial_dir": str(trial_dir),
                        "task": task,
                        "reason": "wrong_model",
                        "model": model,
                    }
                )
                continue
            if obj.get("exception_info") is not None:
                exclusions.append(
                    {"run": run, "trial_dir": str(trial_dir), "task": task, "reason": "exception"}
                )
                continue

            session = F._find_session_jsonl(trial_dir)
            if session is None:
                exclusions.append(
                    {"run": run, "trial_dir": str(trial_dir), "task": task, "reason": "missing_session"}
                )
                continue
            messages, stats = F.parse_messages(session)
            n_tools = int(stats.get("tool_calls", 0))
            n_obs = int(stats.get("tool_obs", 0))
            has_structured = n_tools > 0 and any(
                m.get("role") == "assistant" and m.get("tool_calls") for m in messages
            )
            if not has_structured or not (min_tools <= n_tools <= max_tools):
                exclusions.append(
                    {
                        "run": run,
                        "trial_dir": str(trial_dir),
                        "task": task,
                        "reward": reward,
                        "reason": "tool_count_or_structure",
                        "n_tool_turns": n_tools,
                    }
                )
                continue
            if n_obs < max(1, n_tools // 2):
                exclusions.append(
                    {
                        "run": run,
                        "trial_dir": str(trial_dir),
                        "task": task,
                        "reward": reward,
                        "reason": "insufficient_tool_observations",
                        "n_tool_turns": n_tools,
                        "n_tool_observations": n_obs,
                    }
                )
                continue

            meta = F.TrialMeta(
                run=run,
                trial_dir=str(trial_dir),
                task=task,
                reward=float(reward) if reward is not None else -1.0,
                model=model,
                n_tool_turns=n_tools,
                n_recovered_tools=int(stats.get("recovered_tools", 0)),
                n_steps=int(stats.get("assistant", 0)),
                source_bucket="evolve",
                quality_score=0.0,
                has_structured_tools=has_structured,
                exception=False,
            )
            meta.quality_score = F._quality_score(meta)
            if reward is not None and reward > 0:
                successes.append((meta, messages))
            elif reward == 0.0:
                failures.append((meta, messages))

    return successes, failures, exclusions


def _safe_partial_pairs(
    record: dict[str, Any],
    *,
    max_pairs: int,
    prefix_fraction: float,
) -> list[dict[str, Any]]:
    """Extract conservative non-error tool turns from a failed trajectory."""
    messages = F.normalize_messages_for_chat_template(record["messages"])
    prefix_limit = max(1, int(len(messages) * prefix_fraction))
    pairs: list[dict[str, Any]] = []

    for i, msg in enumerate(messages):
        if i >= prefix_limit or msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue

        observations: list[str] = []
        for following in messages[i + 1 :]:
            if following.get("role") == "assistant":
                break
            if following.get("role") == "tool":
                observations.append(str(following.get("content") or ""))
        if not observations or any(ERROR_OBS_RE.search(obs) for obs in observations):
            continue

        asst = dict(msg)
        response = F.render_assistant(asst)
        if len(response.strip()) < 4:
            continue
        prior = messages[:i]
        pairs.append(
            {
                "prompt": prior,
                "completion": [asst],
                "prompt_text": F.render_chat(prior),
                "response": response,
                "task": record["task"],
                "run": record["run"],
                "reward": 0.0,
                "weight": 0.35,
                "source_bucket": "evolve",
                "quality_score": record["quality_score"],
                "turn_index": i,
                "quality_tier": "partial_nonerror_prefix",
                "selection_reason": (
                    "failed episode, but task passed in another selected round; "
                    "early structured tool call had an immediate non-error observation"
                ),
            }
        )
        if len(pairs) >= max_pairs:
            break
    return pairs


def _dedupe_pairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        left = row.get("prompt_text", row.get("prompt"))
        right = row.get("response", row.get("completion"))
        if not isinstance(left, str):
            left = json.dumps(left, ensure_ascii=False, sort_keys=True)
        if not isinstance(right, str):
            right = json.dumps(right, ensure_ascii=False, sort_keys=True)
        sig = hashlib.sha256((left + "\0" + right).encode()).hexdigest()
        if sig in seen:
            continue
        seen.add(sig)
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Q9_gated_correct_plus_partial")
    ap.add_argument("--rounds", default="0,1,2,3")
    ap.add_argument("--min-tools", type=int, default=2)
    ap.add_argument("--max-tools", type=int, default=60)
    ap.add_argument("--success-per-task", type=int, default=3)
    ap.add_argument("--partial-per-task", type=int, default=1)
    ap.add_argument("--success-pairs-per-traj", type=int, default=8)
    ap.add_argument("--partial-pairs-per-traj", type=int, default=2)
    ap.add_argument("--partial-prefix-fraction", type=float, default=0.7)
    ap.add_argument("--eval-ratio", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rounds = _parse_rounds(args.rounds)
    successes, failures, exclusions = _scan_selected_runs(
        rounds, min_tools=args.min_tools, max_tools=args.max_tools
    )
    success_tasks = {m.task for m, _ in successes}

    holdout_success = [(m, msgs) for m, msgs in successes if m.task in F.HOLDOUT_TASKS]
    train_success = [(m, msgs) for m, msgs in successes if m.task not in F.HOLDOUT_TASKS]
    train_success = F.dedupe_by_task(train_success, per_task=args.success_per_task)

    # Failed episodes are only considered if another selected run proves that the
    # task is solvable by this same model. Holdouts remain excluded.
    partial_candidates = [
        (m, msgs)
        for m, msgs in failures
        if m.task in success_tasks and m.task not in F.HOLDOUT_TASKS
    ]
    partial_candidates = F.dedupe_by_task(
        partial_candidates, per_task=args.partial_per_task
    )

    success_records = [F._messages_to_sft_record(m, msgs) for m, msgs in train_success]
    partial_records = [F._messages_to_sft_record(m, msgs) for m, msgs in partial_candidates]

    correct_pairs: list[dict[str, Any]] = []
    for rec in success_records:
        for pair in F.expand_turn_pairs(rec, max_pairs=args.success_pairs_per_traj):
            pair["quality_tier"] = "correct_trajectory"
            pair["selection_reason"] = "reward=1, no exception, structured tools"
            correct_pairs.append(pair)

    partial_pairs: list[dict[str, Any]] = []
    for rec in partial_records:
        partial_pairs.extend(
            _safe_partial_pairs(
                rec,
                max_pairs=args.partial_pairs_per_traj,
                prefix_fraction=args.partial_prefix_fraction,
            )
        )

    correct_pairs = _dedupe_pairs(correct_pairs)
    partial_pairs = _dedupe_pairs(partial_pairs)
    all_pairs = _dedupe_pairs(correct_pairs + partial_pairs)
    rng = random.Random(args.seed)
    rng.shuffle(all_pairs)
    n_eval = max(1, int(len(all_pairs) * args.eval_ratio)) if all_pairs else 0
    eval_rows = all_pairs[:n_eval]
    train_rows = all_pairs[n_eval:]

    out = OUT_ROOT / args.name
    out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out / "train.jsonl", train_rows)
    _write_jsonl(out / "eval.jsonl", eval_rows)
    _write_jsonl(out / "correct_pairs.jsonl", correct_pairs)
    _write_jsonl(out / "partial_pairs.jsonl", partial_pairs)
    _write_jsonl(out / "trajectories.jsonl", success_records + partial_records)
    _write_jsonl(
        out / "holdout_success_trajectories.jsonl",
        [F._messages_to_sft_record(m, msgs) for m, msgs in holdout_success],
    )
    _write_jsonl(out / "exclusions.jsonl", exclusions)

    summary = {
        "name": args.name,
        "model": MODEL,
        "snapshot_rounds": rounds,
        "source_runs": [f"{RUN_PREFIX}{r}-traj" for r in rounds],
        "n_success_trajectories": len(success_records),
        "n_partial_trajectories": len(partial_records),
        "n_correct_pairs": len(correct_pairs),
        "n_partial_pairs": len(partial_pairs),
        "n_train_pairs": len(train_rows),
        "n_eval_pairs": len(eval_rows),
        "train_pair_tiers": dict(Counter(r["quality_tier"] for r in train_rows)),
        "tasks": sorted({r["task"] for r in success_records + partial_records}),
        "success_tasks": sorted({r["task"] for r in success_records}),
        "partial_tasks": sorted({r["task"] for r in partial_records}),
        "holdout_tasks": sorted(F.HOLDOUT_TASKS),
        "n_holdout_success_trajectories": len(holdout_success),
        "n_exclusions": len(exclusions),
        "partial_definition": (
            "reward=0 with no exception; same task has reward=1 in another selected "
            "Qwen3.5-9B round; train only early structured tool turns whose immediate "
            "tool observations do not match conservative error patterns"
        ),
        "partial_weight_note": (
            "weight=0.35 is metadata only in the current trainer; partial influence is "
            "controlled primarily by max pairs and one trajectory per task"
        ),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    provenance = {
        "builder": str(Path(__file__).resolve()),
        "parameters": vars(args),
        "quality_gates": {
            "completed_15_task_runs_only": True,
            "exact_model": MODEL,
            "no_exception": True,
            "structured_tools": True,
            "min_tools": args.min_tools,
            "max_tools": args.max_tools,
            "holdout_excluded_from_train": sorted(F.HOLDOUT_TASKS),
            "tool_call_format": "qwen3_xml native XML",
        },
    }
    (out / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not correct_pairs:
        print("ERROR: no correct training pairs built", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
