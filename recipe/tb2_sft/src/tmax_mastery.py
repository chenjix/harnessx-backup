#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Which Tmax evolve tasks has the model already mastered?

The coevolve loop rotates its 50-task evolve set every iteration: tasks the
current model already solves *and* whose successful trajectories were already
harvested into an SFT corpus are retired, and fresh taxonomy tasks take their
slots. Keeping them would burn a full rollout budget re-demonstrating what the
model can already do, and would keep feeding the same demos into every
subsequent SFT corpus.

"Mastered" is deliberately conjunctive:

  1. the task appears in ``selected_tasks`` of at least one SFT corpus
     summary.json (i.e. a correct trajectory was actually *used* for SFT), and
  2. it was solved at least ``--min-successes`` times across the evolve rounds
     scanned, with success rate >= ``--min-success-rate``.

(1) alone would retire a task whose one lucky success was filtered out of the
corpus by the tool-count / ctrl-C gates. (2) alone would retire a task the SFT
corpus never actually learned from. Requiring both means a retired task has
both been solved and been taught.

Usage::

  python -m recipe.tb2_sft.src.tmax_mastery \
    --run-tag tmax-coev-rep1-i1 \
    --corpus recipe/tb2_sft/data/tmax_coev_rep1_i1 \
    --min-successes 1 \
    --out outputs/tmax_coevolve/rep1/mastered_i2.json

Output JSON carries ``task_ids`` (the retire list) plus per-task evidence, so a
later audit can tell *why* a task left the pool.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BENCH = _ROOT / ".benchmarks" / "tmax"

# run_eval writes status="ok" on a clean rollout, "agent_error" when the agent
# loop itself failed, "error" when the harness/docker layer raised. Only the
# first counts as a real attempt outcome for mastery purposes.
_OK_STATUSES = {"ok", "success", "passed"}


def load_task_ids(path: Path | None) -> list[str]:
    """Read a task-id list from a plain JSON list or a dict with ids inside."""
    if path is None or not Path(path).is_file():
        return []
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        items: Any = raw
    elif isinstance(raw, dict):
        items = (
            raw.get("task_ids")
            or raw.get("tasks")
            or raw.get("task_names")
            or raw.get("selected_tasks")
            or []
        )
        if not items and all(isinstance(k, str) and k.startswith("task_") for k in raw):
            items = list(raw.keys())
    else:
        items = []
    out: list[str] = []
    for x in items:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            tid = x.get("task_id") or x.get("name") or x.get("id") or x.get("task")
            if tid:
                out.append(str(tid))
    return out


def discover_traj_dirs(
    *, bench_root: Path, run_tags: list[str], traj_dirs: list[Path]
) -> list[Path]:
    found: list[Path] = []
    for tag in run_tags:
        found.extend(sorted(Path(bench_root).glob(f"{tag}-r*-traj")))
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


def scan_task_outcomes(traj_dirs: list[Path]) -> dict[str, dict[str, Any]]:
    """Per-task attempt/success counts across every scanned evolve round."""
    per_task: dict[str, dict[str, Any]] = {}
    for traj_dir in traj_dirs:
        for rp in sorted(Path(traj_dir).glob("*.result.json")):
            if rp.name == "result.json":
                continue
            try:
                result = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                continue
            tid = str(result.get("task_id") or rp.name.replace(".result.json", ""))
            reward = result.get("reward")
            if reward is None:
                vr = result.get("verifier_result") or {}
                reward = (vr.get("rewards") or {}).get("reward")
            status = str(result.get("status") or "ok")
            rec = per_task.setdefault(
                tid,
                {"attempts": 0, "successes": 0, "errors": 0, "runs": []},
            )
            passed = isinstance(reward, (int, float)) and float(reward) > 0
            if status in _OK_STATUSES:
                rec["attempts"] += 1
            else:
                rec["errors"] += 1
            if passed:
                rec["successes"] += 1
            rec["runs"].append(
                {"run": Path(traj_dir).name, "reward": reward, "status": status}
            )
    for rec in per_task.values():
        att = rec["attempts"]
        rec["success_rate"] = (rec["successes"] / att) if att else 0.0
    return per_task


def corpus_selected_tasks(corpus_dirs: list[Path]) -> tuple[set[str], list[str]]:
    """Union of ``selected_tasks`` over each corpus' summary.json."""
    tasks: set[str] = set()
    used: list[str] = []
    for d in corpus_dirs:
        summary = Path(d) / "summary.json" if Path(d).is_dir() else Path(d)
        if not summary.is_file():
            continue
        try:
            obj = json.loads(summary.read_text(encoding="utf-8"))
        except Exception:
            continue
        sel = obj.get("selected_tasks")
        if isinstance(sel, list):
            tasks.update(str(t) for t in sel)
            used.append(str(summary))
    return tasks, used


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-tag", action="append", default=[], help="Evolve run tag (repeatable)")
    ap.add_argument("--traj-dir", action="append", default=[], type=Path, help="Explicit traj dir")
    ap.add_argument("--bench-root", type=Path, default=_DEFAULT_BENCH)
    ap.add_argument(
        "--corpus",
        action="append",
        default=[],
        type=Path,
        help="SFT corpus dir (or its summary.json) whose selected_tasks count as taught (repeatable)",
    )
    ap.add_argument("--min-successes", type=int, default=1)
    ap.add_argument("--min-success-rate", type=float, default=0.0)
    ap.add_argument(
        "--require-corpus",
        dest="require_corpus",
        action="store_true",
        default=True,
        help="Retire only tasks whose trajectories reached an SFT corpus (default)",
    )
    ap.add_argument("--no-require-corpus", dest="require_corpus", action="store_false")
    ap.add_argument(
        "--also-include",
        action="append",
        default=[],
        type=Path,
        help="Previously computed mastered list to carry forward (repeatable)",
    )
    ap.add_argument(
        "--restrict-to",
        type=Path,
        default=None,
        help="Only consider these task ids (e.g. the previous evolve set)",
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    traj_dirs = discover_traj_dirs(
        bench_root=args.bench_root,
        run_tags=list(args.run_tag),
        traj_dirs=list(args.traj_dir or []),
    )
    per_task = scan_task_outcomes(traj_dirs)
    taught, corpus_files = corpus_selected_tasks(list(args.corpus or []))
    restrict = set(load_task_ids(args.restrict_to)) if args.restrict_to else None

    carried: set[str] = set()
    for p in args.also_include or []:
        carried.update(load_task_ids(p))

    mastered: list[str] = []
    evidence: dict[str, Any] = {}
    for tid, rec in sorted(per_task.items()):
        if restrict is not None and tid not in restrict:
            continue
        if args.require_corpus and tid not in taught:
            continue
        if rec["successes"] < args.min_successes:
            continue
        if rec["success_rate"] < args.min_success_rate:
            continue
        mastered.append(tid)
        evidence[tid] = {
            "successes": rec["successes"],
            "attempts": rec["attempts"],
            "success_rate": round(rec["success_rate"], 3),
            "errors": rec["errors"],
            "in_sft_corpus": tid in taught,
        }

    # Carried-forward ids stay retired even if their traj dirs were cleaned up.
    for tid in sorted(carried):
        if tid not in evidence:
            evidence[tid] = {"carried_forward": True}
            mastered.append(tid)

    out = {
        "task_ids": sorted(set(mastered)),
        "n": len(set(mastered)),
        "rule": {
            "require_corpus": args.require_corpus,
            "min_successes": args.min_successes,
            "min_success_rate": args.min_success_rate,
        },
        "traj_dirs": [str(d) for d in traj_dirs],
        "corpus_summaries": corpus_files,
        "n_tasks_seen": len(per_task),
        "n_taught_by_sft": len(taught),
        "n_carried_forward": len(carried),
        "evidence": evidence,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(
        f"mastered={out['n']} (seen={len(per_task)} taught={len(taught)} "
        f"carried={len(carried)} traj_dirs={len(traj_dirs)}) -> {args.out}"
    )
    if not traj_dirs and not carried:
        print(
            "WARNING: no evolve traj dirs matched — nothing can be retired this iteration",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
