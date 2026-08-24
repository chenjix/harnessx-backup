#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Build one iteration's Tmax harness-evolve task set (rotating pool).

The coevolve loop used to evolve on the *same* 50 tasks every iteration. Once
the model reliably solves a task, re-running it every iteration spends a full
rollout slot on a demo the SFT corpus already contains. This builder produces
iteration k's set instead:

    keep  = iteration k-1's set  minus  mastered  minus  holdout
    new   = domain-stratified sample from the taxonomy pool, excluding
            holdout + mastered + keep + anything in --avoid-tasks
    set_k = keep + new,  truncated/padded to --size

and writes the two files the evolve stage needs:

    <out>/task_ids.json                  -> EVOLVE_TASKS_JSON
    <out>/eval_task_set_with_envs.jsonl  -> EVOLVE_ENVS_JSONL
    <out>/summary.json                   -> provenance (kept/new/domains)

Env rows come from ``--taxonomy-parquet`` (the 2.2k taxonomy parquet, which
carries ``container_def`` alongside description and tests) and/or from any
existing ``eval_task_set_with_envs.jsonl`` passed as ``--pool-envs-jsonl``. A row
is only usable if its ``container_def`` is one
``recipe.tmax_eval.singularity_to_dockerfile`` can convert (Bootstrap: docker +
From: + %post) and both ``description`` and ``test_final_state`` are non-empty —
without those ``recipe.tmax_eval.run_eval`` cannot build the task's image, and
drawing such a task would fail the whole round rather than just that task.

Usage::

  python -m recipe.tb2_sft.src.build_tmax_evolve_task_set \
    --name tmax_coev_rep1_i2 --size 50 \
    --keep-tasks recipe/tb2_evolver/tasks_tmax_evolve50_list.json \
    --retire-tasks outputs/tmax_coevolve/rep1/mastered_i2.json \
    --exclude-tasks recipe/tb2_evolver/tasks_tmax_only200.json \
    --pool-envs-jsonl recipe/tb2_sft/data/tmax_task_pool/eval_task_set_with_envs.jsonl \
    --seed 42

  # preflight only: is a usable pool reachable at all?
  python -m recipe.tb2_sft.src.build_tmax_evolve_task_set --check-only ...
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tmax_mastery import load_task_ids  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DATA = _ROOT / "recipe" / "tb2_sft" / "data"
_DEFAULT_TAXONOMY = _ROOT / "data/external/tmax-taxonomy/data/train-00000-of-00001.parquet"
_DEFAULT_HOLDOUT = _ROOT / "recipe/tb2_evolver/tasks_tmax_only200.json"

ENV_FIELDS = (
    "task_id",
    "domain",
    "task_complexity",
    "description",
    "container_def",
    "test_final_state",
    "test_initial_state",
)


def _usable(row: dict[str, Any]) -> str | None:
    """Return None if the row can be evaluated, else the reason it cannot."""
    if not str(row.get("description") or "").strip():
        return "no_description"
    if not str(row.get("test_final_state") or "").strip():
        return "no_test_final_state"
    cdef = str(row.get("container_def") or "")
    if not cdef.strip():
        return "no_container_def"
    try:
        from recipe.tmax_eval.singularity_to_dockerfile import singularity_to_dockerfile

        singularity_to_dockerfile(cdef)
    except ImportError:
        # Fall back to the same two structural checks the converter makes.
        if "From:" not in cdef or "%post" not in cdef:
            return "bad_container_def"
    except Exception:
        return "bad_container_def"
    return None


def _env_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: raw.get(k) for k in ENV_FIELDS}


def load_pool(
    *,
    taxonomy_parquet: Path | None,
    pool_envs: list[Path],
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Build {task_id: env_row} from every source, plus a reject histogram.

    Later sources win, so an explicit ``--pool-envs-jsonl`` can repair a row the
    parquet has in unusable shape.
    """
    rows: dict[str, dict[str, Any]] = {}
    rejects: dict[str, int] = defaultdict(int)

    def _offer(raw: dict[str, Any]) -> None:
        tid = str(raw.get("task_id") or raw.get("id") or "")
        if not tid:
            rejects["no_task_id"] += 1
            return
        row = _env_row(raw)
        reason = _usable(row)
        if reason is not None:
            # Do not let an unusable duplicate evict a usable row.
            if tid not in rows:
                rejects[reason] += 1
            return
        rows[tid] = row

    if taxonomy_parquet is not None and Path(taxonomy_parquet).is_file():
        try:
            import pandas as pd
        except ImportError as e:  # pragma: no cover
            raise SystemExit("ERROR: pandas required to read the taxonomy parquet") from e
        df = pd.read_parquet(taxonomy_parquet)
        for raw in df.to_dict(orient="records"):
            _offer(dict(raw))

    for p in pool_envs:
        p = Path(p)
        if not p.is_file():
            print(f"WARNING: pool envs jsonl not found: {p}", file=sys.stderr)
            continue
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    _offer(json.loads(line))
                except json.JSONDecodeError:
                    rejects["bad_json"] += 1
    return rows, dict(rejects)


def stratified_fill(
    *,
    pool: dict[str, dict[str, Any]],
    candidates: list[str],
    need: int,
    seed: int,
) -> list[str]:
    """Round-robin across domains so a set is not all one domain."""
    if need <= 0 or not candidates:
        return []
    buckets: dict[str, list[str]] = defaultdict(list)
    for tid in candidates:
        buckets[str(pool[tid].get("domain") or "unknown")].append(tid)
    rng = random.Random(seed)
    for b in buckets.values():
        b.sort()  # deterministic before shuffle regardless of dict order
        rng.shuffle(b)
    domains = sorted(buckets)
    picked: list[str] = []
    while len(picked) < need and any(buckets[d] for d in domains):
        for d in domains:
            if len(picked) >= need:
                break
            if buckets[d]:
                picked.append(buckets[d].pop())
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="Output dataset dir name under --out-root")
    ap.add_argument("--out-root", type=Path, default=_DEFAULT_DATA)
    ap.add_argument("--out-dir", type=Path, default=None, help="Explicit output dir (overrides --name)")
    ap.add_argument("--size", type=int, default=50)
    ap.add_argument("--min-size", type=int, default=1, help="Fail if fewer usable tasks than this")
    ap.add_argument("--keep-tasks", action="append", default=[], type=Path,
                    help="Previous set — kept first, in order (repeatable)")
    ap.add_argument("--retire-tasks", action="append", default=[], type=Path,
                    help="Mastered tasks to drop and never re-draw (repeatable)")
    ap.add_argument("--exclude-tasks", action="append", default=[], type=Path,
                    help="Never include (default: holdout-102)")
    ap.add_argument("--avoid-tasks", action="append", default=[], type=Path,
                    help="Do not draw as *new* tasks, but keep if already in --keep-tasks")
    ap.add_argument("--taxonomy-parquet", type=Path, default=_DEFAULT_TAXONOMY)
    ap.add_argument("--no-taxonomy", action="store_true", help="Ignore --taxonomy-parquet")
    ap.add_argument("--pool-envs-jsonl", action="append", default=[], type=Path,
                    help="Existing eval_task_set_with_envs.jsonl to source rows from (repeatable)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--check-only", action="store_true",
                    help="Report pool usability and exit without writing anything")
    ap.add_argument(
        "--min-new",
        type=int,
        default=0,
        help="--check-only: fail unless at least this many *fresh* tasks are drawable. "
        "A pool holding only the tasks already in play passes every other check but "
        "cannot actually rotate anything.",
    )
    args = ap.parse_args()

    excludes = list(args.exclude_tasks) or [_DEFAULT_HOLDOUT]
    exclude: set[str] = set()
    for p in excludes:
        exclude.update(load_task_ids(p))
    retire: set[str] = set()
    for p in args.retire_tasks or []:
        retire.update(load_task_ids(p))
    avoid: set[str] = set()
    for p in args.avoid_tasks or []:
        avoid.update(load_task_ids(p))
    keep_in: list[str] = []
    for p in args.keep_tasks or []:
        for t in load_task_ids(p):
            if t not in keep_in:
                keep_in.append(t)

    pool, rejects = load_pool(
        taxonomy_parquet=None if args.no_taxonomy else args.taxonomy_parquet,
        pool_envs=list(args.pool_envs_jsonl or []),
    )
    print(
        f"pool: {len(pool)} usable task env row(s)"
        + (f"; rejected: {rejects}" if rejects else "")
    )
    if not pool:
        print(
            "ERROR: no usable task env rows. Check that --taxonomy-parquet exists and\n"
            "       carries container_def / description / test_final_state, or pass\n"
            "       --pool-envs-jsonl pointing at an eval_task_set_with_envs.jsonl.",
            file=sys.stderr,
        )
        return 2

    keep = [t for t in keep_in if t in pool and t not in retire and t not in exclude]
    dropped_keep = [t for t in keep_in if t not in keep]
    if len(keep) > args.size:
        print(
            f"NOTE: keep list ({len(keep)}) exceeds --size {args.size}; "
            f"truncating to the first {args.size}"
        )
        keep = keep[: args.size]

    blocked = set(keep) | retire | exclude | avoid
    candidates = sorted(t for t in pool if t not in blocked)
    new = stratified_fill(pool=pool, candidates=candidates, need=args.size - len(keep), seed=args.seed)
    task_ids = keep + new

    if args.check_only:
        print(
            f"check-only: keep={len(keep)} new_available={len(candidates)} "
            f"would_select={len(task_ids)}/{args.size} (min_new={args.min_new})"
        )
        if len(task_ids) < max(1, args.min_size):
            print(
                f"ERROR: only {len(task_ids)} task(s) selectable (min-size={args.min_size})",
                file=sys.stderr,
            )
            return 2
        if len(candidates) < args.min_new:
            print(
                f"ERROR: only {len(candidates)} fresh task(s) drawable, need {args.min_new} "
                "— the pool holds little beyond the tasks already in play",
                file=sys.stderr,
            )
            return 2
        return 0

    if len(task_ids) < args.min_size:
        print(
            f"ERROR: only {len(task_ids)} task(s) available (min-size={args.min_size}); "
            f"pool exhausted after excluding {len(exclude)} holdout + {len(retire)} mastered",
            file=sys.stderr,
        )
        return 2
    if len(task_ids) < args.size:
        print(
            f"WARNING: set is short — {len(task_ids)}/{args.size} tasks "
            f"({len(candidates)} candidate(s) left in pool)",
            file=sys.stderr,
        )

    out_dir = Path(args.out_dir) if args.out_dir else (args.out_root / (args.name or "tmax_evolve_set"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "task_ids.json").write_text(json.dumps(task_ids, indent=2) + "\n", encoding="utf-8")
    envs_path = out_dir / "eval_task_set_with_envs.jsonl"
    with envs_path.open("w", encoding="utf-8") as fh:
        for tid in task_ids:
            fh.write(json.dumps(pool[tid], ensure_ascii=False) + "\n")

    domains: dict[str, int] = defaultdict(int)
    for tid in task_ids:
        domains[str(pool[tid].get("domain") or "unknown")] += 1
    summary = {
        "name": out_dir.name,
        "size_requested": args.size,
        "n_tasks": len(task_ids),
        "n_kept": len(keep),
        "n_new": len(new),
        "kept_tasks": keep,
        "new_tasks": new,
        "retired_from_keep": dropped_keep,
        "n_retired_input": len(retire),
        "n_excluded_input": len(exclude),
        "n_avoided_input": len(avoid),
        "n_pool_usable": len(pool),
        "n_pool_candidates": len(candidates),
        "pool_rejects": rejects,
        "by_domain": dict(sorted(domains.items())),
        "seed": args.seed,
        "sources": {
            "taxonomy_parquet": None if args.no_taxonomy else str(args.taxonomy_parquet),
            "pool_envs_jsonl": [str(p) for p in (args.pool_envs_jsonl or [])],
        },
        "task_ids_json": str(out_dir / "task_ids.json"),
        "envs_jsonl": str(envs_path),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {out_dir}: n_tasks={len(task_ids)} (kept={len(keep)} new={len(new)}) "
        f"domains={dict(sorted(domains.items()))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
