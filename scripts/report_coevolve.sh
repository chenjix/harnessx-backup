#!/usr/bin/env bash
set -Eeuo pipefail
# Read the co-evolution chain: does each stage actually improve on the last?
#
# The chain a single replicate walks is:
#
#   R0_anchor(1)  model_0 + harness_0     <- the starting point
#   B(1)          model_0 + harness_1     <- harness moved, model held
#   E(1)          model_1 + harness_1     <- model moved, harness held
#   R0_anchor(2)  model_1 + harness_1     <- SAME condition as E(1), re-measured
#   B(2)          model_1 + harness_2
#   E(2)          model_2 + harness_2
#   ...
#
# Two things make this readable that a raw score list does not:
#
#  * Each step changes exactly ONE side, so a delta is attributable. A single
#    number per iteration cannot distinguish "harness helped, SFT hurt equally"
#    from "nothing happened".
#  * R0_anchor(k) re-measures the identical condition E(k-1) already measured.
#    Their difference is pure noise — it is a free, in-chain estimate of the
#    error bar every other delta has to be judged against, taken on the same
#    host in the same window. Reported as `noise` below.
#
# Usage: bash scripts/report_coevolve.sh [rep...]      (default: 1 2 3)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REPS=("${@:-1 2 3}")
read -r -a REPS <<<"${REPS[*]}"

PY="${PYTHON_BIN:-}"
[[ -n "$PY" ]] || { [[ -x "${VLLM_VENV:-$HOME/.venv}/bin/python" ]] \
  && PY="${VLLM_VENV:-$HOME/.venv}/bin/python" || PY="$(command -v python3)"; }

"$PY" - "$ROOT" "${REPS[@]}" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]); reps = sys.argv[2:]
STAGES = ("R0_anchor", "B_eval_harness", "E_eval_sft")

def load(rep):
    f = root / "outputs/loop3" / f"rep{rep}" / "scores.tsv"
    if not f.is_file():
        return {}
    out = {}
    for line in f.read_text().splitlines()[1:]:
        p = line.split("\t")
        if len(p) < 6:
            continue
        it, stage, model, harness, passed, total = p[0], p[1], p[2], p[3], p[4], p[5]
        try:
            out[(int(it), stage)] = (int(passed), int(total), model, harness)
        except ValueError:
            pass
    return out

def fmt(d):
    return f"{d:+d}" if d is not None else "  ?"

noise_samples, h_deltas, s_deltas = [], [], []
per_rep_chain = {}
n_suspect = 0


def suspect(passed, total):
    """A 0/28 is an infrastructure failure, not a measurement.

    It is what a run looks like when every container failed to start — observed
    for real when the image-pull filesystem filled up. Folding those into the
    deltas produced a "harness step" of -7.9 tasks, which is not a property of
    any harness. Excluded from the pooled statistics and flagged in the table
    rather than dropped silently, so the gap in the chain stays visible.
    """
    return total > 0 and passed == 0

for rep in reps:
    rows = load(rep)
    if not rows:
        print(f"rep{rep}: no scores yet"); continue
    iters = sorted({k[0] for k in rows})
    print(f"\n{'='*74}\nrep{rep}\n{'='*74}")
    print(f"{'iter':>4}  {'stage':<16} {'condition':<26} {'score':>7}  {'Δ vs prev':>9}")
    prev = None
    chain = []
    for it in iters:
        for st in STAGES:
            v = rows.get((it, st))
            if v is None:
                continue
            passed, total, model, harness = v
            bad = suspect(passed, total)
            prev_bad = prev is None or suspect(prev, total)
            # A delta is only meaningful when BOTH endpoints are real measurements.
            d = None if (bad or prev_bad) else passed - prev
            model_short = model.split("/")[0] if "/" in model else model
            cond = f"{model_short} + {harness}"
            mark = ""
            if bad:
                n_suspect += 1
                mark = "  ** SUSPECT: 0 passed — infrastructure failure, excluded"
            elif st == "R0_anchor" and it > 1:
                mark = "  <- repeat of E(%d): noise" % (it - 1)
                if d is not None:
                    noise_samples.append(d)
            elif st == "B_eval_harness" and d is not None:
                h_deltas.append(d); mark = "  harness"
            elif st == "E_eval_sft" and d is not None:
                s_deltas.append(d); mark = "  SFT"
            print(f"{it:>4}  {st:<16} {cond:<26} {passed:>3}/{total:<3}  {fmt(d):>9}{mark}")
            chain.append((it, st, passed, bad))
            prev = passed
    per_rep_chain[rep] = chain
    good = [c for c in chain if not c[3]]
    if good:
        first, last = good[0][2], good[-1][2]
        span = f"{good[0][1]}(iter {good[0][0]}) -> {good[-1][1]}(iter {good[-1][0]})"
        print(f"\n  net over measured stages [{span}]: {first} -> {last}  ({fmt(last-first)})")
    if len(good) != len(chain):
        print(f"  ({len(chain)-len(good)} stage(s) excluded as infrastructure failures)")

def stats(xs):
    if not xs:
        return "n=0"
    m = sum(xs)/len(xs)
    sd = (sum((x-m)**2 for x in xs)/(len(xs)-1))**0.5 if len(xs) > 1 else float("nan")
    return f"n={len(xs)}  mean={m:+.2f}  sd={sd:.2f}  values={xs}"

print(f"\n{'='*74}\npooled across replicates\n{'='*74}")
if n_suspect:
    print(f"  {n_suspect} stage(s) scored 0 and were excluded — those runs measured the")
    print(f"  infrastructure, not the model. Re-run them before drawing conclusions.\n")
print(f"  noise (same condition, re-measured) : {stats(noise_samples)}")
print(f"  harness step  (B - previous)        : {stats(h_deltas)}")
print(f"  SFT step      (E - B)               : {stats(s_deltas)}")

if noise_samples:
    m = sum(abs(x) for x in noise_samples)/len(noise_samples)
    print(f"\n  A step is only readable if it clears the noise: mean |noise| = {m:.1f} tasks.")
    for name, xs in (("harness", h_deltas), ("SFT", s_deltas)):
        if not xs:
            continue
        mm = sum(xs)/len(xs)
        same_sign = all(x >= 0 for x in xs) or all(x <= 0 for x in xs)
        verdict = ("consistent and above noise" if same_sign and abs(mm) > m
                   else "consistent but within noise" if same_sign
                   else "inconsistent in sign — not a signal")
        print(f"    {name:<8}: mean {mm:+.2f}, signs {'aligned' if same_sign else 'mixed'} -> {verdict}")
else:
    print("\n  No in-chain noise estimate yet (needs iteration 2's R0_anchor).")
PY
