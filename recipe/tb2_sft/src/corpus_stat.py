#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Print `n_trajectories own_pairs` for a built corpus, space-separated.

Exists as a file rather than as `python -c "..."` inside the loop driver because
that inline form sat inside a single-quoted `bash -c '...'` block, where a Python
string literal written with single quotes silently closes the shell's quoting:
`s['n_trajectories']` reached Python as `s[n_trajectories]` and raised NameError
after the (expensive) routing stage had already succeeded. A separate file has no
quoting layers to get wrong.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: corpus_stat.py <corpus_dir>/summary.json", file=sys.stderr)
        return 2
    s = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    n_traj = s["n_trajectories"]
    # `own_pairs` is only written when a Tmax top-up ran; for an own-only build
    # the same quantity is the train+eval total, since every pair is own-sourced.
    own_pairs = s.get("own_pairs") or (s["n_train_pairs"] + s["n_eval_pairs"])
    print(f"{n_traj} {own_pairs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
