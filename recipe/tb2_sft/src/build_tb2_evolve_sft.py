#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Build a LoRA SFT corpus from TB2 (terminal-bench) harness-evolve trajectories.

TB2 counterpart of ``build_tmax_evolve_sft.py``. The selection policy is shared
verbatim -- per-task cap, uncapped selection, quality ranking, and the same
``summary.json`` schema the coevolve driver reads -- so a TB2.1 chain and a Tmax
chain differ in the benchmark, not in how their corpora get assembled.

Only the scanner differs, because the two benchmarks lay trajectories out
differently:

  Tmax  ``.benchmarks/tmax/<tag>-r<N>-traj/<task>.result.json``
                                          ``<task>.messages.json``
  TB2   ``.benchmarks/tb2/<tag>-r<N>-traj/<task>__<trial>/result.json``
                                                        ``agent/oh_runs/*.jsonl``

The Tmax builder globs flat ``*.result.json`` and so finds nothing under a TB2
job dir. The TB2 readers already exist in ``filter_and_build_sft``
(``_find_session_jsonl`` plus ``parse_messages``, which folds the HarnessX
session event stream into OpenAI-style messages); this module wires them to the
shared policy.

Filters (the Tmax gates, plus two the TB2 layout makes necessary):
  - verifier_result.rewards.reward > 0
  - exception_info is null -- a crashed trial is not a demonstration
  - structured assistant tool_calls present
  - min_tools <= n_tools <= max_tools (default 2..60)
  - at least ~half the tool calls have a resolvable observation, so sessions
    whose tool output was never flushed do not become training noise
  - no Ctrl-C in the transcript
  - optional holdout-task exclusion
  - up to ``--per-task`` demos per task, then ``--max-trajs`` overall
    (<= 0 keeps every survivor, the default)

Example:
  python -m recipe.tb2_sft.src.build_tb2_evolve_sft \\
    --run-tag tb21-coev-rep1-i1 \\
    --name tb21_coev_rep1_i1 \\
    --exclude-tasks recipe/tb2_evolver/tasks_tb21_holdout.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import build_tmax_evolve_sft as T  # noqa: E402
import filter_and_build_sft as F  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BENCH = _ROOT / ".benchmarks" / "tb2"
_DEFAULT_DATA = _ROOT / "recipe" / "tb2_sft" / "data"


def _discover_traj_dirs(
    *,
    run_tags: list[str],
    traj_dirs: list[Path],
    bench_root: Path,
) -> list[Path]:
    """Resolve ``<tag>-r<N>-traj`` round dirs, never their per-task shards.

    The sharded eval writes each task to ``<tag>-r<N>-traj__task-<name>/`` and
    lands a second, full copy in the merged ``<tag>-r<N>-traj/``. Both are real
    directories -- not symlinks -- so picking up both would count every
    trajectory twice. The ``-traj`` suffix anchors the end of the name, which
    excludes the shards; ``scan_successes`` also dedupes on trial name.
    """
    found: list[Path] = []
    for tag in run_tags:
        found.extend(sorted(bench_root.glob(f"{tag}-r*-traj")))
    for d in traj_dirs:
        p = Path(d)
        if p.is_dir():
            found.append(p)
    seen: set[Path] = set()
    out: list[Path] = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def _span_seconds(section: Any) -> float | None:
    if not isinstance(section, dict):
        return None
    started, finished = section.get("started_at"), section.get("finished_at")
    if not (isinstance(started, str) and isinstance(finished, str)):
        return None
    try:
        t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = (t1 - t0).total_seconds()
    return delta if delta > 0 else None


def _elapsed_seconds(obj: dict[str, Any]) -> float | None:
    """Seconds the agent itself ran, falling back to the whole-trial span.

    Harbor records only start/finish timestamps per phase. ``agent_execution``
    is the phase worth ranking on; the top-level span also covers the image
    pull and the verifier, which say nothing about how cleanly the model solved
    the task.
    """
    return _span_seconds(obj.get("agent_execution")) or _span_seconds(obj)


def scan_successes(
    traj_dirs: list[Path],
    *,
    min_tools: int,
    max_tools: int,
    exclude_tasks: set[str],
) -> tuple[list[tuple[F.TrialMeta, list[dict[str, Any]]]], dict[str, int]]:
    stats = {
        "trial_dirs": 0,
        "duplicate_trials": 0,
        "no_result": 0,
        "reward_pass": 0,
        "exception": 0,
        "missing_messages": 0,
        "tool_gate": 0,
        "obs_gate": 0,
        "ctrl_c": 0,
        "holdout_excluded": 0,
        "kept_raw": 0,
    }
    kept: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []
    seen_trials: set[str] = set()

    for traj_dir in traj_dirs:
        run_name = traj_dir.name
        for trial_dir in sorted(traj_dir.iterdir()):
            # `_routing/` and `_ROUTED_EVIDENCE.md` are evolve bookkeeping.
            if not trial_dir.is_dir() or trial_dir.name.startswith("_"):
                continue
            stats["trial_dirs"] += 1
            # Trial names carry a random suffix, so they stay distinct across
            # rounds; a repeat means the same trial was reached by two paths.
            if trial_dir.name in seen_trials:
                stats["duplicate_trials"] += 1
                continue
            seen_trials.add(trial_dir.name)

            obj = F._load_json(trial_dir / "result.json")
            if not obj:
                stats["no_result"] += 1
                continue

            reward = F._reward_of(obj)
            if reward is None or reward <= 0:
                continue
            stats["reward_pass"] += 1

            # A trial that raised never closed out its episode; the transcript
            # stops mid-thought and would teach the model to do the same.
            if obj.get("exception_info") is not None:
                stats["exception"] += 1
                continue

            task_id = str(obj.get("task_name") or trial_dir.name.split("__")[0])
            if task_id in exclude_tasks:
                stats["holdout_excluded"] += 1
                continue

            jsonl = F._find_session_jsonl(trial_dir)
            if jsonl is None:
                stats["missing_messages"] += 1
                continue
            messages, mstats = F.parse_messages(jsonl)
            if not messages:
                stats["missing_messages"] += 1
                continue

            if T._has_ctrl_c(messages):
                stats["ctrl_c"] += 1
                continue

            n_tools = int(mstats.get("tool_calls", 0))
            has_struct = n_tools > 0 and any(
                m.get("role") == "assistant" and m.get("tool_calls") for m in messages
            )
            if not has_struct or not (min_tools <= n_tools <= max_tools):
                stats["tool_gate"] += 1
                continue
            # Tool output can live in a `content_ref` side file that a killed run
            # never wrote. Without paired observations the next assistant turn
            # reads as unmotivated, which is worse than dropping the trajectory.
            if int(mstats.get("tool_obs", 0)) < max(1, n_tools // 2):
                stats["obs_gate"] += 1
                continue

            meta = F.TrialMeta(
                run=run_name,
                trial_dir=str(trial_dir),
                task=task_id,
                reward=float(reward),
                model=F._model_of(obj),
                n_tool_turns=n_tools,
                n_recovered_tools=int(mstats.get("recovered_tools", 0)),
                n_steps=int(mstats.get("assistant", 0)),
                # Mirrors the Tmax builder's "<bench>_evolve" marker. Every traj
                # here comes from an evolve round, so the bucket is uniform and
                # ranking is decided by the quality score alone.
                source_bucket="tb2_evolve",
                quality_score=0.0,
                has_structured_tools=True,
                exception=False,
            )
            meta.quality_score = F._quality_score(meta)
            elapsed = _elapsed_seconds(obj)
            if elapsed is not None:
                # Same tie-breaker as the Tmax builder: prefer quick clean solves.
                meta.quality_score += max(0.0, 1.0 - min(elapsed, 600.0) / 600.0) * 0.2
            kept.append((meta, messages))
            stats["kept_raw"] += 1

    return kept, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-tag", action="append", default=[], help="Evolve run tag (repeatable)")
    ap.add_argument("--traj-dir", action="append", default=[], type=Path, help="Explicit traj dir")
    ap.add_argument("--bench-root", type=Path, default=_DEFAULT_BENCH)
    ap.add_argument("--name", required=True, help="Dataset name under recipe/tb2_sft/data/")
    ap.add_argument("--out-root", type=Path, default=_DEFAULT_DATA)
    # Floor is per-benchmark: a TB2.1 evolve set is ~25 tasks against Tmax's 50,
    # so the Tmax default of 80 could never be met and would fire top-ups forever.
    ap.add_argument("--min-trajs", type=int, default=40)
    ap.add_argument(
        "--max-trajs",
        type=int,
        default=0,
        help="Cap on selected trajectories; <= 0 keeps every survivor (default)",
    )
    ap.add_argument(
        "--per-task",
        type=int,
        default=3,
        help="Max demos kept per task_id across rounds (default 3)",
    )
    ap.add_argument("--min-tools", type=int, default=2)
    ap.add_argument("--max-tools", type=int, default=60)
    ap.add_argument("--max-pairs-per-traj", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--exclude-tasks",
        type=Path,
        default=None,
        help="Holdout task list to exclude from SFT (strongly recommended)",
    )
    ap.add_argument("--no-exclude-holdout", action="store_true")
    ap.add_argument("--eval-ratio", type=float, default=0.05)
    args = ap.parse_args()

    if not args.run_tag and not args.traj_dir:
        ap.error("pass --run-tag and/or --traj-dir")

    exclude: set[str] = set()
    if not args.no_exclude_holdout:
        exclude = T._load_task_ids(args.exclude_tasks)
        if not exclude:
            print(
                "WARNING: no holdout tasks excluded — the corpus may contain tasks "
                "the holdout eval scores, which would inflate it. Pass "
                "--exclude-tasks, or --no-exclude-holdout to say you meant it.",
                file=sys.stderr,
            )

    traj_dirs = _discover_traj_dirs(
        run_tags=list(args.run_tag),
        traj_dirs=list(args.traj_dir or []),
        bench_root=args.bench_root,
    )
    if not traj_dirs:
        print(f"ERROR: no trajectory dirs found under {args.bench_root}", file=sys.stderr)
        return 2

    print(f"scanning {len(traj_dirs)} traj dir(s):")
    for d in traj_dirs:
        print(f"  - {d}")

    raw, scan_stats = scan_successes(
        traj_dirs,
        min_tools=args.min_tools,
        max_tools=args.max_tools,
        exclude_tasks=exclude,
    )
    # Selection policy is shared with the Tmax builder on purpose.
    deduped = T.dedupe_by_task(raw, per_task=args.per_task)
    selected = T.select_trajs(deduped, max_trajs=args.max_trajs, seed=args.seed)

    if len(selected) < args.min_trajs:
        print(
            f"WARNING: only {len(selected)} unique success trajs "
            f"(wanted >= {args.min_trajs}). Proceeding anyway.",
            file=sys.stderr,
        )

    pairs = T.build_pairs(selected, max_pairs_per_traj=args.max_pairs_per_traj)
    if not pairs:
        print("ERROR: no supervised pairs produced", file=sys.stderr)
        return 2

    train, ev = F.split_pairs(pairs, eval_ratio=args.eval_ratio, seed=args.seed)
    if not ev:
        # Tiny corpus: peel one pair so train_sft.sh's require_file passes.
        ev = train[-1:]
        train = train[:-1] or ev

    out = args.out_root / args.name
    out.mkdir(parents=True, exist_ok=True)
    F.write_jsonl(out / "train.jsonl", train)
    F.write_jsonl(out / "eval.jsonl", ev)

    # Schema matches the Tmax builder: the driver reads `n_selected_trajs` for
    # the corpus floor, and the rotation script reads `selected_tasks` to retire
    # tasks that have already handed over a correct trajectory.
    summary = {
        "name": args.name,
        "benchmark": "tb2",
        "traj_dirs": [str(d) for d in traj_dirs],
        "scan": scan_stats,
        "n_raw_kept": len(raw),
        "n_unique_tasks": len({m.task for m, _ in deduped}),
        "n_after_per_task_cap": len(deduped),
        "n_selected_trajs": len(selected),
        "per_task": args.per_task,
        "n_train_pairs": len(train),
        "n_eval_pairs": len(ev),
        "min_trajs": args.min_trajs,
        "max_trajs": args.max_trajs,
        "min_tools": args.min_tools,
        "max_tools": args.max_tools,
        "exclude_holdout_n": len(exclude),
        "selected_tasks": [m.task for m, _ in selected],
        "selected_meta": [
            {
                "task": m.task,
                "run": m.run,
                "n_tools": m.n_tool_turns,
                "quality_score": m.quality_score,
            }
            for m, _ in selected
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "selected_trials.json").write_text(
        json.dumps([asdict(m) for m, _ in selected], indent=2),
        encoding="utf-8",
    )

    print(
        f"wrote {out}: trajs={len(selected)} "
        f"train_pairs={len(train)} eval_pairs={len(ev)} "
        f"(raw_pass_gate={scan_stats['kept_raw']} "
        f"unique_tasks={len({m.task for m, _ in deduped})} "
        f"after_per_task={len(deduped)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
