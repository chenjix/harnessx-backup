#!/usr/bin/env bash
set -euo pipefail
# Compare conditions across replicate rounds, reporting the PER-ROUND delta
# rather than a difference of averages.
#
# Why per-round: TB2 outcomes move with machine state (the 900 s agent timeout
# means a task that passes on a quiet host fails on a busy one), so the absolute
# level of every condition drifts together between rounds. Averaging first buries
# that shared drift inside both numbers; differencing within a round cancels it,
# which is the entire point of running the conditions concurrently.
#
# Expects job dirs named <prefix>-r<N> under .benchmarks/tb2/, e.g.
#   rep-base-r1  rep-base-r2  rep-base-r3
#   rep-routed400-r1 ...
#
# Usage:
#   bash scripts/report_replicates.sh rep-base rep-routed400 [rep-mix ...]
#   ROUNDS=3 bash scripts/report_replicates.sh rep-base rep-routed400

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
(($# >= 1)) || { echo "usage: report_replicates.sh <prefix> [<prefix> ...]" >&2; exit 2; }
TASKS_JSON="${TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_sample16_seed42_act15.json}"
ROUNDS="${ROUNDS:-3}"

PY="${PYTHON_BIN:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "${VLLM_VENV:-$HOME/.venv}/bin/python" ]]; then PY="${VLLM_VENV:-$HOME/.venv}/bin/python"
  else PY="$(command -v python3)"; fi
fi

"$PY" - "$ROOT" "$TASKS_JSON" "$ROUNDS" "$@" <<'PYEOF'
import json, statistics, sys
from pathlib import Path

root, tasks_json, rounds = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
prefixes = sys.argv[4:]
tasks = json.loads(tasks_json.read_text())
bench = root / ".benchmarks" / "tb2"
N = len(tasks)


def read(job_dir: Path):
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
    missing = []
    agg = job_dir / "result.json"
    if agg.is_file():
        try:
            missing = json.loads(agg.read_text()).get("missing_tasks") or []
        except Exception:
            pass
    return per, missing


data: dict[str, dict[int, tuple]] = {}
for pref in prefixes:
    data[pref] = {}
    for r in range(1, rounds + 1):
        got = read(bench / f"{pref}-r{r}")
        if got is not None:
            data[pref][r] = got

print(f"\n=== replicate summary (n={N} tasks/round) ===\n")
hdr = "  " + "round".ljust(8) + "".join(p.ljust(18) for p in prefixes)
print(hdr)
print("  " + "-" * (len(hdr) - 2))
counts: dict[str, dict[int, int]] = {p: {} for p in prefixes}
for r in range(1, rounds + 1):
    cells = ""
    for p in prefixes:
        if r in data[p]:
            per, missing = data[p][r]
            n = sum(per.values())
            counts[p][r] = n
            cells += f"{n}/{N}{'*' if missing else ''}".ljust(18)
        else:
            cells += "(not run)".ljust(18)
    print("  " + f"r{r}".ljust(8) + cells)

print("  " + "-" * (len(hdr) - 2))
means = ""
for p in prefixes:
    v = list(counts[p].values())
    means += (f"{statistics.mean(v):.2f}" if v else "-").ljust(18)
print("  " + "mean".ljust(8) + means)
spread = ""
for p in prefixes:
    v = list(counts[p].values())
    spread += (f"{min(v)}-{max(v)}" if v else "-").ljust(18)
print("  " + "range".ljust(8) + spread)
if any(data[p].get(r, ((), []))[1] for p in prefixes for r in data[p]):
    print("\n  * = that round had task(s) producing no result (counted as failures)")

# ── per-round deltas against the first prefix ────────────────────────────────
if len(prefixes) >= 2:
    base = prefixes[0]
    print(f"\n=== per-round delta vs `{base}` (this is the number to read) ===\n")
    for p in prefixes[1:]:
        rs = sorted(set(counts[base]) & set(counts[p]))
        if not rs:
            print(f"  {p:20s} no round has both conditions — cannot pair")
            continue
        deltas = [counts[p][r] - counts[base][r] for r in rs]
        detail = "  ".join(f"r{r}:{d:+d}" for r, d in zip(rs, deltas))
        print(f"  {p:20s} {detail}    mean Δ = {statistics.mean(deltas):+.2f}")
        if len(deltas) >= 2:
            print(f"  {'':20s} paired rounds={len(deltas)}  sd(Δ)={statistics.stdev(deltas):.2f}")
        # Shared drift: how much the baseline itself moved across rounds.
        bv = [counts[base][r] for r in rs]
        print(f"  {'':20s} baseline moved {min(bv)}..{max(bv)} across those rounds "
              f"(shared drift the pairing removes)")

    print(
        "\n  Read the per-round Δ, not the difference of means: the absolute level of every\n"
        "  condition drifts together with machine state, and only the within-round Δ has\n"
        "  that drift cancelled. A |mean Δ| under ~1 task is not distinguishable from noise\n"
        "  at 1 trial/task (reference/results/REPORT-2026-07-26.md)."
    )

# ── per-task stability ───────────────────────────────────────────────────────
print("\n=== per-task stability (Y=passed, .=failed, -=round not run) ===\n")
w = max(len(t) for t in tasks) + 2
cols = [(p, r) for p in prefixes for r in sorted(data[p])]
print("  " + "task".ljust(w) + "".join(f"{p.replace('rep-','')[:9]}r{r}".ljust(13) for p, r in cols))
print("  " + "-" * (w + 13 * len(cols)))
for t in tasks:
    row = ""
    for p, r in cols:
        per, _ = data[p][r]
        row += ("Y" if per.get(t) else ("." if t in per else "-")).ljust(13)
    print("  " + t.ljust(w) + row)

print("\n  always-pass / never-pass tasks carry no signal; the churning ones are where")
print("  the noise lives.\n")
PYEOF
