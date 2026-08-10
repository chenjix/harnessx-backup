#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Mine the Tmax success corpus for error->recovery demonstrations, indexed by
error class, so the harness evolver can be shown what a successful recovery
looks like on the same error our model failed to recover from.

Why this exists
---------------
The routed loop's harness-evolution branch runs dry: measured on two independent
trajectory sets, unambiguous harness-side evidence is a median of 0 per round.
The obvious fix — "route Tmax data too, then the harness side won't be zero" —
does not work, for two reasons that are worth writing down so nobody retries it:

  1. Every Tmax config we hold is publisher-filtered to successes
     (`..._only_success`, 5,795 rows; the 15k open-instruct split carries no
     reward field at all; `tmax-taxonomy` holds task definitions, not
     trajectories). A success cannot produce a `harness_evolution` attribution:
     fault_router routes passes to the SFT destinations by construction.
  2. Even given Tmax failures, they were produced by Qwen3.6-27B running
     *allenai's* harness. A swallowed error there is a defect in their tool
     wrapper. Our meta-agent edits our YAML; it cannot fix, and must not be told
     to fix, a defect observed somewhere else.

What does transfer is the *recovery behaviour*. 62% of Tmax successes contain at
least one error observation the model then recovered from — ~2.9 demonstrations
per such trajectory. Our own model-side failures are dominated by
`ignored_visible_error_repeated_action` and `visible_error_no_effective_recovery`
on the same error classes. Pairing the two produces a question the meta-agent can
legitimately act on:

    On `command not found`, a stronger model recovers by probing PATH and
    installing the missing package. Ours re-issues the identical command.
    Given the current harness, is that recovery path reachable — is the error
    surfaced in full, does any tool description hint at the probe?

That is an affordance question about the harness, derived without ever claiming
a Tmax trajectory demonstrates a defect in our harness.

Output: a JSON index {error_class: [exemplar, ...]}, each exemplar carrying the
error observation, the recovery action taken, and how many steps until success.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARQUET = (ROOT / "data/external/tmax-sft-only-success"
                   / "skill_tax_20260505_2.2k_combined_balanced_thinking_only_success"
                   / "train-00000-of-00001.parquet")
DEFAULT_OUT = ROOT / "recipe/tb2_evolver/tmax_recovery_index.json"

# Error classes, ordered most-specific first: a "command not found" observation
# also matches the generic `error` pattern, and classifying it as the latter
# would merge it with unrelated failures and make the exemplars useless.
ERROR_CLASSES: list[tuple[str, re.Pattern[str]]] = [
    ("command_not_found", re.compile(r"command not found|: not found", re.I)),
    ("module_not_found", re.compile(r"ModuleNotFoundError|ImportError|No module named", re.I)),
    ("no_such_file", re.compile(r"no such file or directory", re.I)),
    ("permission_denied", re.compile(r"permission denied|operation not permitted", re.I)),
    ("package_install_failed", re.compile(r"unable to locate package|E: |apt-get.*fail|pip.*(ERROR|failed)", re.I)),
    ("connection_refused", re.compile(r"connection refused|could not resolve host|network is unreachable", re.I)),
    ("timeout", re.compile(r"timed? ?out|timeout", re.I)),
    ("compile_or_build_error", re.compile(r"\b(undefined reference|make(\[\d+\])?: \*\*\*|compilation terminated"
                                          r"|fatal error:|linker command failed)", re.I)),
    ("python_traceback", re.compile(r"Traceback \(most recent call last\)", re.I)),
    ("test_assertion_failed", re.compile(r"AssertionError|FAILED |assert ", re.I)),
    ("syntax_error", re.compile(r"SyntaxError|unexpected token|parse error", re.I)),
    ("invalid_argument", re.compile(r"invalid (option|argument|choice)|unrecognized option|usage:", re.I)),
    ("generic_error", re.compile(r"\berror\b|\bexception\b|\bfailed\b|\bcannot\b|\bunable to\b", re.I)),
]


def classify(text: str) -> str | None:
    for name, rx in ERROR_CLASSES:
        if rx.search(text):
            return name
    return None


def _cmd_of(msg: dict[str, Any]) -> str:
    """Extract the shell command from an assistant turn's tool call."""
    tcs = msg.get("tool_calls")
    if tcs is None:
        return ""
    try:
        tcs = list(tcs)
    except TypeError:
        return ""
    for tc in tcs:
        fn = (tc or {}).get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                return args[:400]
        if isinstance(args, dict):
            for k in ("command", "cmd", "input", "script"):
                if args.get(k):
                    return str(args[k])[:400]
    return ""


def _normalize_action(cmd: str) -> str:
    """Reduce a command to its shape so exemplars group by strategy, not by path."""
    c = re.sub(r"/\S+", "<path>", cmd)
    c = re.sub(r"\b\d+\b", "<n>", c)
    return re.sub(r"\s+", " ", c).strip()[:120]


def build_index(parquet: Path, *, per_class: int = 8, max_rows: int | None = None) -> dict[str, Any]:
    import pandas as pd

    df = pd.read_parquet(parquet)
    if max_rows:
        df = df.head(max_rows)

    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_shapes: dict[str, set[str]] = defaultdict(set)
    stats = Counter()

    for row_i, msgs in enumerate(df["messages"]):
        ms = list(msgs)
        for i, m in enumerate(ms):
            if m.get("role") != "tool":
                continue
            obs = str(m.get("content") or "")
            if not obs.strip():
                continue
            cls = classify(obs)
            if cls is None:
                continue
            # The recovery is the next assistant turn that issues a command. If
            # none exists the trajectory ended here, so despite the overall
            # success this particular error was not recovered *from* — it was
            # the last thing that happened, and it teaches nothing.
            recovery, n_after = "", 0
            for j in range(i + 1, len(ms)):
                if ms[j].get("role") == "assistant":
                    c = _cmd_of(ms[j])
                    if c:
                        recovery = c
                        n_after = sum(1 for k in range(j, len(ms)) if ms[k].get("role") == "assistant")
                        break
            if not recovery:
                stats["error_at_end_no_recovery"] += 1
                continue

            failed_cmd = ""
            for j in range(i - 1, -1, -1):
                if ms[j].get("role") == "assistant":
                    failed_cmd = _cmd_of(ms[j])
                    break

            shape = _normalize_action(recovery)
            stats[f"class:{cls}"] += 1
            # Keep exemplars *diverse in strategy*: 8 copies of `apt-get install`
            # teach the meta-agent nothing 1 copy does not. Dedup on the
            # normalized recovery shape, not on the raw command.
            if shape in seen_shapes[cls] or len(by_class[cls]) >= per_class:
                continue
            seen_shapes[cls].add(shape)
            by_class[cls].append(dict(
                error_class=cls,
                failed_action=failed_cmd[:300],
                observation=obs[:400],
                recovery_action=recovery[:300],
                recovery_shape=shape,
                assistant_turns_to_success=n_after,
                source_row=int(row_i),
            ))

    return dict(
        source=str(parquet),
        note=("Recovery demonstrations mined from Tmax SUCCESSES. These are not "
              "evidence of a defect in our harness. They answer: what does a "
              "successful recovery on this error class look like?"),
        n_rows_scanned=len(df),
        counts={k: v for k, v in sorted(stats.items())},
        exemplars_per_class={k: len(v) for k, v in sorted(by_class.items())},
        index={k: v for k, v in sorted(by_class.items())},
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parquet", default=str(DEFAULT_PARQUET))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--per-class", type=int, default=8)
    ap.add_argument("--max-rows", type=int, default=None)
    args = ap.parse_args()

    p = Path(args.parquet)
    if not p.is_file():
        raise SystemExit(f"Tmax parquet not found: {p}")
    idx = build_index(p, per_class=args.per_class, max_rows=args.max_rows)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(idx, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"scanned {idx['n_rows_scanned']} Tmax trajectories -> {out}")
    print("\n  recovery demonstrations found per error class:")
    for k, v in sorted(idx["counts"].items()):
        if k.startswith("class:"):
            print(f"    {v:>6}  {k[6:]}")
    print("\n  distinct recovery strategies kept per class:")
    for k, v in idx["exemplars_per_class"].items():
        print(f"    {v:>6}  {k}")
    skipped = idx["counts"].get("error_at_end_no_recovery", 0)
    print(f"\n  {skipped} error observations skipped (nothing followed them — no recovery to learn)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
