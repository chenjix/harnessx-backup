#!/usr/bin/env bash
# Project coevolve wall-clock from a measured timing probe.
#
#   PROBE_JOB=probe-base-16 bash scripts/tmax/estimate_runtime.sh
#   PROBE_JOB=probe-base-16 N_ITERS=3 TMAX_CONCURRENT=16 HOLDOUT_CONCURRENT=16 \
#     META_MINUTES=20 SFT_MINUTES=45 bash scripts/tmax/estimate_runtime.sh
#
# Reads per-task elapsed_s out of .benchmarks/tmax/$PROBE_JOB/summary.json (run
# the probe from the README command first) and turns it into per-stage and
# per-iteration estimates using the same knobs the loop will run with. Rollout
# time is the only quantity that has to be measured on the node; everything else
# is arithmetic over it.

set -euo pipefail
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

PROBE_JOB="${PROBE_JOB:?set PROBE_JOB=<job name under .benchmarks/tmax>}"
PY="${PYTHON_BIN:-${VLLM_VENV:-$HOME/.venv}/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3)"

PROBE_JOB="$PROBE_JOB" \
N_ITERS="${N_ITERS:-3}" EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}" \
EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}" MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}" \
EVOLVE_SET_SIZE="${EVOLVE_SET_SIZE:-50}" HOLDOUT_N="${HOLDOUT_N:-102}" \
TMAX_CONCURRENT="${TMAX_CONCURRENT:-16}" HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-16}" \
META_MINUTES="${META_MINUTES:-20}" SFT_MINUTES="${SFT_MINUTES:-45}" \
POOL_START_MINUTES="${POOL_START_MINUTES:-6}" ROOT="$ROOT" "$PY" - <<'PY'
import json, math, os
from pathlib import Path

E = os.environ
job = E["PROBE_JOB"]
p = Path(E["ROOT"]) / ".benchmarks" / "tmax" / job / "summary.json"
if not p.is_file():
    raise SystemExit(f"ERROR: no probe summary at {p} — run the timing probe first")
s = json.loads(p.read_text())
rows = s.get("results") or []
el = sorted(float(r.get("elapsed_s") or 0) for r in rows if r.get("elapsed_s"))
if not el:
    raise SystemExit(f"ERROR: no elapsed_s in {p}")

def q(f):
    return el[min(len(el) - 1, int(len(el) * f))]

p50, p90, mx = q(0.5), q(0.9), el[-1]
mean = sum(el) / len(el)
n_ok = sum(1 for r in rows if r.get("status") == "ok")

# A wave-count model underestimates: a wave ends with its slowest task, so use
# mean throughput and take max(wave model, straggler floor).
def pass_minutes(n_tasks, conc):
    waves = math.ceil(n_tasks / conc)
    by_throughput = n_tasks * mean / conc / 60
    by_straggler = waves * p90 / 60
    return max(by_throughput, by_straggler)

ev_n, ho_n = int(E["EVOLVE_SET_SIZE"]), int(E["HOLDOUT_N"])
ev_c, ho_c = int(E["TMAX_CONCURRENT"]), int(E["HOLDOUT_CONCURRENT"])
meta, sft, pool = float(E["META_MINUTES"]), float(E["SFT_MINUTES"]), float(E["POOL_START_MINUTES"])
rounds, extra, retries, iters = (
    int(E["EVOLVE_ROUNDS"]), int(E["EXTRA_EVOLVE_ROUNDS"]),
    int(E["MAX_SFT_RETRIES"]), int(E["N_ITERS"]),
)

ev_round = pass_minutes(ev_n, ev_c) + meta
ho_pass = pass_minutes(ho_n, ho_c) + pool
stage_A = pool + rounds * ev_round
best = stage_A + ho_pass + sft + ho_pass                    # B + D + E, model accepted first try
worst = best + retries * (pool + extra * ev_round + sft + ho_pass)

print(f"probe {job}: n={len(el)} tasks, status_ok={n_ok}/{len(rows)}, "
      f"passed={s.get('n_passed')}/{s.get('n_tasks')}")
print(f"  per-task seconds  mean={mean:.0f} p50={p50:.0f} p90={p90:.0f} max={mx:.0f}")
print()
print(f"  evolve round ({ev_n} tasks @{ev_c} + {meta:.0f}m meta) : {ev_round:.0f} min")
print(f"  holdout pass ({ho_n} tasks @{ho_c} + {pool:.0f}m pool) : {ho_pass:.0f} min")
print(f"  stage A ({rounds} rounds)                        : {stage_A/60:.1f} h")
print(f"  SFT                                        : {sft:.0f} min")
print()
print(f"  iteration, model accepted first try        : {best/60:.1f} h  "
      f"(A + B + SFT + E)")
print(f"  iteration, {retries} SFT retries used             : {worst/60:.1f} h")
print(f"  {iters} iterations, typical                     : {iters*best/60:.1f} h")
print(f"  {iters} iterations, all retries used            : {iters*worst/60:.1f} h")
print()
print(f"  holdout-102 passes per iteration           : 2 (B+E) .. {2+retries} (B+E*{1+retries})")
print(f"  102-task evals total across {iters} iters        : {2*iters} .. {(2+retries)*iters}")
PY
