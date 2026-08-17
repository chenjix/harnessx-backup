#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Build a quick LoRA SFT corpus from Tmax harness-evolve trajectories.

Scans ``.benchmarks/tmax/<run_tag>-r*-traj/`` (or explicit traj dirs), keeps
successful high-quality non-duplicate trajectories, expands them into
conversational prompt/completion pairs (same shape as the proven parquet SFT
pipeline), and writes ``train.jsonl`` / ``eval.jsonl`` / ``summary.json``.

Filters (defaults match the proven Tmax SFT gates):
  - reward == 1 and status == ok
  - messages.json present with structured tool_calls
  - min_tools <= n_tools <= max_tools (default 2..60)
  - dedupe by task_id (keep highest quality_score)
  - optional holdout-task exclusion
  - select up to ``--max-trajs`` (default 100), at least warn if < ``--min-trajs``

Example:
  python -m recipe.tb2_sft.src.build_tmax_evolve_sft \\
    --run-tag tmax-ev9b50-20260814-042904 \\
    --name tmax_coevolve_i1 \\
    --min-trajs 50 --max-trajs 100
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import filter_and_build_sft as F  # noqa: E402

_CTRL_C_RE = re.compile(r"\x03|ctrl[_-]?c", re.IGNORECASE)
_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BENCH = _ROOT / ".benchmarks" / "tmax"
_DEFAULT_DATA = _ROOT / "recipe" / "tb2_sft" / "data"


def _load_task_ids(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    items = raw if isinstance(raw, list) else raw.get("tasks") or raw.get("task_names") or []
    if isinstance(raw, dict) and not items:
        items = list(raw.keys())
    for x in items:
        if isinstance(x, str):
            out.add(x)
        elif isinstance(x, dict):
            tid = x.get("task_id") or x.get("name") or x.get("id") or x.get("task")
            if tid:
                out.add(str(tid))
    return out


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize evolve-traj messages into filter_and_build_sft's internal shape."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(m.get("tool_call_id") or ""),
                    "name": str(m.get("name") or "Bash"),
                    "content": m.get("content") or "[empty tool result]",
                }
            )
            continue
        if role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            tcs = m.get("tool_calls")
            if tcs:
                fixed = []
                for i, tc in enumerate(tcs):
                    tc = dict(tc)
                    fn = dict(tc.get("function") or {})
                    name = str(fn.get("name") or "Bash")
                    if name.lower() == "bash":
                        name = "Bash"
                    args = fn.get("arguments")
                    if isinstance(args, dict):
                        arg_str = json.dumps(args, ensure_ascii=False)
                    else:
                        arg_str = str(args or "{}")
                    fixed.append(
                        {
                            "id": str(tc.get("id") or f"call_{i}"),
                            "type": "function",
                            "function": {"name": name, "arguments": arg_str},
                        }
                    )
                item["tool_calls"] = fixed
            out.append(item)
            continue
        out.append({"role": role, "content": m.get("content") or ""})
    return out


def _n_tools(messages: list[dict[str, Any]]) -> int:
    return sum(len(m.get("tool_calls") or []) for m in messages if m.get("role") == "assistant")


def _has_ctrl_c(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        if _CTRL_C_RE.search(str(m.get("content") or "")):
            return True
        for tc in m.get("tool_calls") or []:
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            if _CTRL_C_RE.search(str(fn.get("arguments") or "")):
                return True
    return False


def _discover_traj_dirs(
    *,
    run_tags: list[str],
    traj_dirs: list[Path],
    bench_root: Path,
) -> list[Path]:
    found: list[Path] = []
    for tag in run_tags:
        found.extend(sorted(bench_root.glob(f"{tag}-r*-traj")))
    for d in traj_dirs:
        p = Path(d)
        if p.is_dir():
            found.append(p)
    # stable unique
    seen: set[Path] = set()
    out: list[Path] = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def scan_successes(
    traj_dirs: list[Path],
    *,
    min_tools: int,
    max_tools: int,
    exclude_tasks: set[str],
) -> tuple[list[tuple[F.TrialMeta, list[dict[str, Any]]]], dict[str, int]]:
    stats = {
        "result_files": 0,
        "reward_pass": 0,
        "missing_messages": 0,
        "bad_status": 0,
        "tool_gate": 0,
        "ctrl_c": 0,
        "holdout_excluded": 0,
        "kept_raw": 0,
    }
    kept: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []

    for traj_dir in traj_dirs:
        run_name = traj_dir.name
        for rp in sorted(traj_dir.glob("*.result.json")):
            if rp.name == "result.json":
                continue
            stats["result_files"] += 1
            try:
                result = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                continue
            reward = result.get("reward")
            if reward is None:
                vr = result.get("verifier_result") or {}
                reward = (vr.get("rewards") or {}).get("reward")
            if not isinstance(reward, (int, float)) or float(reward) <= 0:
                continue
            stats["reward_pass"] += 1

            status = str(result.get("status") or "ok")
            if status not in {"ok", "success", "passed"}:
                stats["bad_status"] += 1
                continue

            task_id = str(result.get("task_id") or rp.name.replace(".result.json", ""))
            if task_id in exclude_tasks:
                stats["holdout_excluded"] += 1
                continue

            mp = traj_dir / f"{task_id}.messages.json"
            if not mp.is_file():
                # some layouts use stem without task_ prefix mismatch
                alt = traj_dir / rp.name.replace(".result.json", ".messages.json")
                mp = alt if alt.is_file() else mp
            if not mp.is_file():
                stats["missing_messages"] += 1
                continue

            try:
                raw_msgs = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                stats["missing_messages"] += 1
                continue
            if isinstance(raw_msgs, dict):
                raw_msgs = raw_msgs.get("messages") or []
            if not isinstance(raw_msgs, list) or not raw_msgs:
                stats["missing_messages"] += 1
                continue

            messages = _normalize_messages(raw_msgs)
            if _has_ctrl_c(messages):
                stats["ctrl_c"] += 1
                continue

            n_tools = _n_tools(messages)
            has_struct = n_tools > 0 and any(
                m.get("role") == "assistant" and m.get("tool_calls") for m in messages
            )
            if not has_struct or not (min_tools <= n_tools <= max_tools):
                stats["tool_gate"] += 1
                continue

            meta = F.TrialMeta(
                run=run_name,
                trial_dir=str(mp),
                task=task_id,
                reward=float(reward),
                model=str(result.get("model") or "evolve"),
                n_tool_turns=n_tools,
                n_recovered_tools=0,
                n_steps=int(result.get("n_steps") or result.get("steps") or len(messages)),
                source_bucket="tmax_evolve",
                quality_score=0.0,
                has_structured_tools=True,
                exception=False,
            )
            meta.quality_score = F._quality_score(meta)
            # Prefer cleaner, shorter successes slightly (faster / less noisy demos).
            elapsed = result.get("elapsed_s")
            if isinstance(elapsed, (int, float)) and elapsed > 0:
                meta.quality_score += max(0.0, 1.0 - min(float(elapsed), 600.0) / 600.0) * 0.2
            kept.append((meta, messages))
            stats["kept_raw"] += 1

    return kept, stats


def dedupe_by_task(
    rows: list[tuple[F.TrialMeta, list[dict[str, Any]]]],
    *,
    per_task: int = 1,
) -> list[tuple[F.TrialMeta, list[dict[str, Any]]]]:
    """Keep up to ``per_task`` highest-quality successes per task_id.

    ``per_task=1`` is strict unique-task dedupe. Raising it (e.g. 2–3) is how
    a 50-task evolve set can still yield 50–100 demos across rounds without
    cloning identical traces — different rounds usually differ.
    """
    buckets: dict[str, list[tuple[F.TrialMeta, list[dict[str, Any]]]]] = {}
    for meta, msgs in rows:
        buckets.setdefault(meta.task, []).append((meta, msgs))
    out: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []
    for task, items in buckets.items():
        items.sort(key=lambda x: (-x[0].quality_score, abs(x[0].n_tool_turns - 15), x[0].run))
        # Drop near-duplicates: same n_tools and same first assistant snippet.
        uniq: list[tuple[F.TrialMeta, list[dict[str, Any]]]] = []
        seen_sig: set[str] = set()
        for meta, msgs in items:
            first_asst = next((m for m in msgs if m.get("role") == "assistant"), {})
            sig = f"{meta.n_tool_turns}|{str(first_asst.get('content') or '')[:120]}"
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
            uniq.append((meta, msgs))
            if len(uniq) >= max(1, per_task):
                break
        out.extend(uniq)
    return out


def select_trajs(
    rows: list[tuple[F.TrialMeta, list[dict[str, Any]]]],
    *,
    max_trajs: int,
    seed: int,
) -> list[tuple[F.TrialMeta, list[dict[str, Any]]]]:
    ranked = sorted(rows, key=lambda x: (-x[0].quality_score, x[0].n_tool_turns, x[0].task))
    if len(ranked) <= max_trajs:
        return ranked
    # Keep top half by quality, sample the rest for diversity.
    keep_top = max(max_trajs // 2, 1)
    head = ranked[:keep_top]
    tail = ranked[keep_top:]
    rng = random.Random(seed)
    need = max_trajs - len(head)
    if need > 0:
        head.extend(rng.sample(tail, min(need, len(tail))))
    return head[:max_trajs]


def build_pairs(
    rows: list[tuple[F.TrialMeta, list[dict[str, Any]]]],
    *,
    max_pairs_per_traj: int,
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for meta, messages in rows:
        record = {
            "messages": messages,
            "task": meta.task,
            "run": meta.run,
            "reward": meta.reward,
            "weight": 1.0,
            "source_bucket": meta.source_bucket,
            "quality_score": meta.quality_score,
        }
        pairs.extend(F.expand_turn_pairs(record, max_pairs=max_pairs_per_traj))
    # Light pair-level dedupe on (task, response prefix).
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for p in pairs:
        key = f"{p.get('task')}|{str(p.get('response') or '')[:240]}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-tag", action="append", default=[], help="Evolve run tag (repeatable)")
    ap.add_argument("--traj-dir", action="append", default=[], type=Path, help="Explicit traj dir")
    ap.add_argument("--bench-root", type=Path, default=_DEFAULT_BENCH)
    ap.add_argument("--name", required=True, help="Dataset name under recipe/tb2_sft/data/")
    ap.add_argument("--out-root", type=Path, default=_DEFAULT_DATA)
    ap.add_argument("--min-trajs", type=int, default=20)
    ap.add_argument("--max-trajs", type=int, default=100)
    ap.add_argument(
        "--per-task",
        type=int,
        default=3,
        help="Max demos kept per task_id across rounds (default 3 → up to ~3× solved tasks)",
    )
    ap.add_argument("--min-tools", type=int, default=2)
    ap.add_argument("--max-tools", type=int, default=60)
    ap.add_argument("--max-pairs-per-traj", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--exclude-tasks",
        type=Path,
        default=_ROOT / "recipe" / "tb2_evolver" / "tasks_tmax_only200.json",
        help="Holdout task list to exclude from SFT (default: 102 holdout)",
    )
    ap.add_argument("--no-exclude-holdout", action="store_true")
    ap.add_argument("--eval-ratio", type=float, default=0.05)
    args = ap.parse_args()

    if not args.run_tag and not args.traj_dir:
        ap.error("pass --run-tag and/or --traj-dir")

    exclude = set() if args.no_exclude_holdout else _load_task_ids(args.exclude_tasks)
    traj_dirs = _discover_traj_dirs(
        run_tags=list(args.run_tag),
        traj_dirs=list(args.traj_dir or []),
        bench_root=args.bench_root,
    )
    if not traj_dirs:
        print("ERROR: no trajectory dirs found", file=sys.stderr)
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
    deduped = dedupe_by_task(raw, per_task=args.per_task)
    selected = select_trajs(deduped, max_trajs=args.max_trajs, seed=args.seed)

    if len(selected) < args.min_trajs:
        print(
            f"WARNING: only {len(selected)} unique success trajs "
            f"(wanted >= {args.min_trajs}). Proceeding anyway.",
            file=sys.stderr,
        )

    pairs = build_pairs(selected, max_pairs_per_traj=args.max_pairs_per_traj)
    if not pairs:
        print("ERROR: no supervised pairs produced", file=sys.stderr)
        return 2

    train, ev = F.split_pairs(pairs, eval_ratio=args.eval_ratio, seed=args.seed)
    if not ev:
        # tiny corpus: peel one pair for eval.jsonl so train_sft.sh's require_file passes
        ev = train[-1:]
        train = train[:-1] or ev

    out = args.out_root / args.name
    out.mkdir(parents=True, exist_ok=True)
    F.write_jsonl(out / "train.jsonl", train)
    F.write_jsonl(out / "eval.jsonl", ev)

    summary = {
        "name": args.name,
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
    # also dump trial metas for debugging
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
