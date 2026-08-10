#!/usr/bin/env bash
set -euo pipefail
# Score the 2x2 {base, SFT} x {baseline harness, evolved harness} for a run tag,
# with the per-task breakdown that makes "same count, different tasks" visible.
#
# Usage: bash scripts/report_2x2.sh <RUN_TAG>

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_TAG="${1:?usage: report_2x2.sh <RUN_TAG>}"
TASKS_JSON="${TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_sample16_seed42_act15.json}"

PY="${PYTHON_BIN:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "${VLLM_VENV:-$HOME/.venv}/bin/python" ]]; then PY="${VLLM_VENV:-$HOME/.venv}/bin/python"
  else PY="$(command -v python3)"; fi
fi

"$PY" - "$ROOT" "$RUN_TAG" "$TASKS_JSON" <<'PYEOF'
import json, sys
from pathlib import Path

root, run_tag, tasks_json = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
tasks = json.loads(tasks_json.read_text())
bench = root / ".benchmarks" / "tb2"

CELLS = [
    ("base + baseline", f"eval-base-preevolve-{run_tag}"),
    ("base + evolved",  f"eval-base-evolved-{run_tag}"),
    ("SFT  + baseline", f"eval-sft-r0h-{run_tag}"),
    ("SFT  + evolved",  f"eval-sft-evolved-{run_tag}"),
]

def score(job_dir: Path):
    """Return ({task: passed}, missing_list) or None when the cell is absent."""
    if not job_dir.is_dir():
        return None
    per = {}
    for p in sorted(job_dir.iterdir()):
        rp = p / "result.json"
        if not (p.is_dir() and rp.is_file()):
            continue
        try:
            o = json.loads(rp.read_text())
        except Exception:
            continue
        t = o.get("task_name") or p.name.split("__")[0]
        r = (o.get("verifier_result") or {}).get("rewards", {}).get("reward")
        per[t] = bool(isinstance(r, (int, float)) and r > 0)
    agg = job_dir / "result.json"
    missing = []
    if agg.is_file():
        try:
            missing = json.loads(agg.read_text()).get("missing_tasks") or []
        except Exception:
            pass
    return per, missing

results = {}
print(f"\n=== 2x2 for {run_tag}  (n={len(tasks)} tasks) ===\n")
for label, job in CELLS:
    got = score(bench / job)
    if got is None:
        print(f"  {label:16s}  (not run)")
        continue
    per, missing = got
    results[label] = per
    n = sum(per.values())
    warn = f"   [!] {len(missing)} task(s) produced no result" if missing else ""
    print(f"  {label:16s}  {n:>2}/{len(tasks)} = {n/len(tasks):.3f}{warn}")

if len(results) >= 2:
    labels = list(results)
    w = max(len(t) for t in tasks) + 2
    print("\n  per-task (Y = passed):\n")
    print("  " + "task".ljust(w) + "".join(l.strip().ljust(18) for l in labels))
    print("  " + "-" * (w + 18 * len(labels)))
    for t in tasks:
        row = "".join(("Y" if results[l].get(t) else ("." if t in results[l] else "?")).ljust(18) for l in labels)
        print("  " + t.ljust(w) + row)
    print("\n  (. = failed, ? = task absent from that eval)")

    # Effect sizes only where both cells of a comparison exist.
    def n_of(l):
        return sum(results[l].values()) if l in results else None
    print("\n  isolated effects:")
    pairs = [
        ("harness effect on base model", "base + baseline", "base + evolved"),
        ("harness effect on SFT model",  "SFT  + baseline", "SFT  + evolved"),
        ("SFT effect on baseline harness", "base + baseline", "SFT  + baseline"),
        ("SFT effect on evolved harness",  "base + evolved",  "SFT  + evolved"),
    ]
    for name, a, b in pairs:
        na, nb = n_of(a), n_of(b)
        if na is None or nb is None:
            print(f"    {name:32s} n/a (missing cell)")
            continue
        d = nb - na
        print(f"    {name:32s} {na} -> {nb}  ({d:+d} task{'s' if abs(d)!=1 else ''})")
    print(
        "\n  NOTE: n=15 at 1 trial/task has a documented run-to-run noise floor of\n"
        "  +/-1-2 tasks (reference/results/REPORT-2026-07-26.md), so any delta of\n"
        "  1-2 tasks is not distinguishable from noise. Use paired replicates\n"
        "  under matched load before treating a difference as real."
    )
print()
PYEOF
