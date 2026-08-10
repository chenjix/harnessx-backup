#!/usr/bin/env bash
set -euo pipefail
# Score the full-89-task 2x2 produced by scripts/run_cell_89.sh.
#
# Separate from report_2x2.sh because that one reads the job names the pipeline
# emits (eval-base-preevolve-<tag>, eval-sft-r0h-<tag>, ...) over the 15-task
# sample, while run_cell_89.sh emits full89-<cell>[-<arm>]-r<N> over all 89.
#
# Usage:
#   bash scripts/report_2x2_89.sh [SFT_ARM] [ROUND]
#     SFT_ARM defaults to routed400, ROUND to 1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SFT_ARM="${1:-routed400}"
ROUND="${2:-1}"
TASKS_JSON="${TASKS_JSON:-$ROOT/recipe/tb2_evolver/tasks_all_tb2.json}"

PY="${PYTHON_BIN:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "${VLLM_VENV:-$HOME/.venv}/bin/python" ]]; then PY="${VLLM_VENV:-$HOME/.venv}/bin/python"
  else PY="$(command -v python3)"; fi
fi

"$PY" - "$ROOT" "$TASKS_JSON" "$SFT_ARM" "$ROUND" <<'PYEOF'
import json, sys
from pathlib import Path

root, tasks_json, arm, rnd = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
tasks = json.loads(tasks_json.read_text())
N = len(tasks)
bench = root / ".benchmarks" / "tb2"

CELLS = [
    ("base + baseline", f"full89-base-baseline-r{rnd}"),
    ("base + evolved",  f"full89-base-evolved-r{rnd}"),
    ("SFT  + baseline", f"full89-sft-baseline-{arm}-r{rnd}"),
    ("SFT  + evolved",  f"full89-sft-evolved-{arm}-r{rnd}"),
]


def read(job: str):
    d = bench / job
    if not d.is_dir():
        return None
    per, health = {}, {"no_agent_tokens": [], "exceptions": {}}
    for p in sorted(d.iterdir()):
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
        # Infrastructure-failure fingerprints. A task whose agent never emitted a
        # token did not "fail the task" — its environment never came up. Scoring
        # those as genuine failures is how a disk-exhausted run reported 2/89.
        ar = o.get("agent_result") or {}
        if not ar.get("n_input_tokens"):
            health["no_agent_tokens"].append(t)
        exc = o.get("exception_info")
        if exc:
            et = exc.get("exception_type") if isinstance(exc, dict) else str(type(exc).__name__)
            health["exceptions"].setdefault(str(et), []).append(t)
    missing = []
    agg = d / "result.json"
    if agg.is_file():
        try:
            missing = json.loads(agg.read_text()).get("missing_tasks") or []
        except Exception:
            pass
    return per, missing, health


res: dict[str, dict] = {}
health: dict[str, dict] = {}
print(f"\n=== full-89 2x2   SFT arm={arm}   round={rnd}   (n={N}) ===\n")
for label, job in CELLS:
    got = read(job)
    if got is None:
        print(f"  {label:16s}  (not run)      {job}")
        continue
    per, missing, h = got
    res[label] = per
    health[label] = h
    n = sum(per.values())
    warn = f"   [!] {len(missing)} produced no result (counted as fail)" if missing else ""
    print(f"  {label:16s}  {n:>3}/{N} = {n/N:.3f}   scored={len(per)}{warn}")

# ── run integrity: is this even a measurement of the model? ──────────────────
print("\n=== run integrity ===\n")
INVALID_PCT = 5.0
tainted = []
for label in res:
    h = health[label]
    n_noagent = len(h["no_agent_tokens"])
    n_exc = sum(len(v) for v in h["exceptions"].values())
    pct = 100.0 * n_noagent / max(N, 1)
    status = "OK" if pct <= INVALID_PCT and n_exc <= N * 0.1 else "TAINTED"
    if status == "TAINTED":
        tainted.append(label)
    print(f"  {label:16s}  {status:8s} agent-never-ran={n_noagent}/{N} ({pct:.0f}%)  "
          f"exceptions={n_exc}")
    for et, ts in sorted(h["exceptions"].items(), key=lambda kv: -len(kv[1]))[:3]:
        print(f"  {'':16s}    {len(ts):>3}x {et}")

if tainted:
    print(
        "\n  *** DO NOT READ THE SCORES ABOVE AS MODEL RESULTS ***\n"
        f"  Tainted cells: {', '.join(tainted)}\n"
        "  A task whose agent emitted zero tokens never had a working environment;\n"
        "  counting it as a failure understates the model by however many such tasks\n"
        "  there are. The usual cause is Docker's filesystem filling mid-run, which\n"
        "  makes `docker compose up` fail during the image pull. Re-run those cells\n"
        "  with TB2_DELETE_IMAGES=1 and a periodic sweep (run_cell_89.sh defaults),\n"
        "  after confirming free space on `docker info --format '{{.DockerRootDir}}'`."
    )
else:
    print("\n  all cells clean — environments came up and agents ran everywhere")

if len(res) < 2:
    print("\n  need at least two cells to compare.\n")
    raise SystemExit(0)


def n_of(l):
    return sum(res[l].values()) if l in res else None


print("\n=== isolated effects (task counts) ===\n")
PAIRS = [
    ("harness effect on base", "base + baseline", "base + evolved"),
    ("harness effect on SFT",  "SFT  + baseline", "SFT  + evolved"),
    ("SFT effect on baseline harness", "base + baseline", "SFT  + baseline"),
    ("SFT effect on evolved harness",  "base + evolved",  "SFT  + evolved"),
]
for name, a, b in PAIRS:
    na, nb = n_of(a), n_of(b)
    if na is None or nb is None:
        print(f"  {name:34s} n/a (missing cell)")
        continue
    # Discordant pairs are what a paired test actually consumes; the raw count
    # difference can hide a large amount of two-way churn.
    a_only = sum(1 for t in tasks if res[a].get(t) and not res[b].get(t))
    b_only = sum(1 for t in tasks if res[b].get(t) and not res[a].get(t))
    print(f"  {name:34s} {na:>3} -> {nb:<3} ({nb-na:+d})"
          f"   discordant={a_only + b_only} (lost {a_only}, gained {b_only})")

print(
    "\n  With n=89 the discordant count is the number a McNemar-style test consumes;"
    "\n  ~10+ is where such a test starts to have usable power. A net delta built from"
    "\n  a large two-way churn is weaker evidence than the same delta from few flips."
)

# Interaction: does the harness effect depend on the model, or vice versa?
if len(res) == 4:
    h_base = n_of("base + evolved") - n_of("base + baseline")
    h_sft = n_of("SFT  + evolved") - n_of("SFT  + baseline")
    print(f"\n  harness effect: {h_base:+d} on base vs {h_sft:+d} on SFT"
          f"   -> interaction {h_sft - h_base:+d}")
    print("  (a large interaction means the two interventions cannot be read independently)")

print("\n=== tasks by pattern ===\n")
labels = [l for l, _ in CELLS if l in res]
buckets: dict[str, list[str]] = {}
for t in tasks:
    key = "".join("Y" if res[l].get(t) else "." for l in labels)
    buckets.setdefault(key, []).append(t)
print("  pattern over: " + " | ".join(l.replace("  ", " ") for l in labels))
for key in sorted(buckets, key=lambda k: (-k.count("Y"), k)):
    ts = buckets[key]
    tag = ""
    if key.count("Y") == 0:
        tag = "  (never passes — no signal)"
    elif key.count("Y") == len(labels):
        tag = "  (always passes — no signal)"
    print(f"\n  {key}{tag}   {len(ts)} task(s)")
    if key.count("Y") not in (0, len(labels)):
        for t in sorted(ts):
            print(f"      {t}")
print()
PYEOF
