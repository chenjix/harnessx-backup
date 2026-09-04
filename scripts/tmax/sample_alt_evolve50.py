#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Sample a fresh 50-task evolve seed that does not overlap prior 9b coevolve sets.

Prior chains (rep1 frozen-50, rep10 rotate) both started from
``tasks_tmax_evolve50_list.json``. This writes a disjoint, domain-stratified
replacement plus the exclusion list later iterations should keep applying.

  python scripts/tmax/sample_alt_evolve50.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SFT_SRC = _ROOT / "recipe" / "tb2_sft" / "src"
if str(_SFT_SRC) not in sys.path:
    sys.path.insert(0, str(_SFT_SRC))

import build_tmax_rl_dataset as B  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "build_iter_evolve_tasks",
    _ROOT / "scripts" / "tmax" / "build_iter_evolve_tasks.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
load_ids = _mod.load_ids

HOLDOUT = _ROOT / "recipe/tb2_evolver/tasks_tmax_only200.json"
SEED50 = _ROOT / "recipe/tb2_evolver/tasks_tmax_evolve50_list.json"
TAX = _ROOT / "data/external/tmax-taxonomy/data/train-00000-of-00001.parquet"
OUT_EXCLUDE = _ROOT / "recipe/tb2_evolver/tasks_tmax_evolve_used_rep1_rep10.json"
OUT_SEED = _ROOT / "recipe/tb2_evolver/tasks_tmax_evolve50_alt12_list.json"
OUT_PROV = _ROOT / "recipe/tb2_evolver/tasks_tmax_evolve50_alt12_provenance.json"
SEED = 77
N = 50

PRIOR_EVOLVESETS = [
    _ROOT / "recipe/tb2_sft/data/tmax_coev_rep1_i1_evolveset/iter_selection.json",
    _ROOT / "recipe/tb2_sft/data/tmax_coev_rep1_i2_evolveset/iter_selection.json",
    _ROOT / "recipe/tb2_sft/data/tmax_coev_rep1_i3_evolveset/iter_selection.json",
    _ROOT / "recipe/tb2_sft/data/tmax_coev_rep9_i1_evolveset/iter_selection.json",
    _ROOT / "recipe/tb2_sft/data/tmax_coev_rep10_i1_evolveset/iter_selection.json",
    _ROOT / "recipe/tb2_sft/data/tmax_coev_rep10_i2_evolveset/iter_selection.json",
    _ROOT / "recipe/tb2_sft/data/tmax_coev_rep10_i3_evolveset/iter_selection.json",
]


def main() -> int:
    holdout = set(load_ids(HOLDOUT))
    seed50 = set(load_ids(SEED50))
    prior: set[str] = set(seed50)
    sources = [str(SEED50)]
    for p in PRIOR_EVOLVESETS:
        if not p.is_file():
            continue
        ids = load_ids(p)
        prior.update(ids)
        sources.append(f"{p} (+{len(ids)})")

    exclude_for_fill = holdout | prior
    records = B.select_from_taxonomy(
        taxonomy_parquet=TAX,
        exclude=exclude_for_fill,
        prefer_ids=[],
        n_tasks=N,
        seed=SEED,
    )
    chosen = [r["ground_truth"] for r in records]
    overlap_holdout = set(chosen) & holdout
    overlap_prior = set(chosen) & prior
    if overlap_holdout or overlap_prior:
        raise SystemExit(
            f"sampler leaked holdout={len(overlap_holdout)} prior={len(overlap_prior)}"
        )
    if len(chosen) != N:
        raise SystemExit(f"expected {N} tasks, got {len(chosen)}")

    OUT_EXCLUDE.write_text(json.dumps(sorted(prior), indent=2) + "\n", encoding="utf-8")
    OUT_SEED.write_text(json.dumps(chosen, indent=2) + "\n", encoding="utf-8")

    import pandas as pd

    df = pd.read_parquet(TAX)
    by = {str(x): str(d) for x, d in zip(df["task_id"], df["domain"])}
    mix = Counter(by.get(t, "?") for t in chosen)

    prov = {
        "n_tasks": len(chosen),
        "seed": SEED,
        "task_ids": chosen,
        "domain_mix": dict(sorted(mix.items())),
        "n_excluded_holdout": len(holdout),
        "n_excluded_prior_evolve": len(prior),
        "overlap_with_seed50": 0,
        "overlap_with_holdout": 0,
        "prior_sources": sources,
        "exclude_file": str(OUT_EXCLUDE),
        "note": (
            "Iter-1 evolve seed for 9b coevolve alt50 (REPLICATE=12). "
            "Disjoint from holdout-102 and every evolve set used by rep1/rep9/rep10."
        ),
    }
    OUT_PROV.write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT_SEED}  n={len(chosen)}")
    print(f"wrote {OUT_EXCLUDE}  n_prior={len(prior)}")
    print(f"domain mix: {dict(sorted(mix.items()))}")
    print(f"holdout={len(holdout)} prior_evolve={len(prior)} overlap_seed50=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
