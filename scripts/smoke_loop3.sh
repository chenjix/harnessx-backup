#!/usr/bin/env bash
set -Eeuo pipefail
# Fast end-to-end smoke test for run_loop_step3.sh — minutes, no GPU.
#
# Builds a shell-level fake ROOT out of symlinks, drops stub evolve/evaluate/
# train_sft scripts into its scripts/ dir, and runs the real loop driver against
# them for all N iterations. The CPU stages (routing, corpus build, score
# extraction, harness/adapter chaining, resume markers, the lock) run for real
# against real quarantined trajectories; only the three GPU stages are faked.
#
# The stubs ASSERT the environment they are handed, so a mis-wired variable
# fails here in seconds rather than three hours into a real chain — which is
# exactly how the last two defects were found (a quoting bug in the corpus stage
# and a silently-swallowed error trap), both after a full evolve had been spent.
#
# Usage:  bash scripts/smoke_loop3.sh [n_iters]
# Leaves nothing behind except the smoke root, which it removes on success.

REAL="$(cd "$(dirname "$0")/.." && pwd)"
N_ITERS="${1:-3}"
REP="${SMOKE_REPLICATE:-99}"          # 99 keeps every path clear of real chains
SMOKE="$REAL/.smoke_root"
FAKE_TRAJ_SRC="${FAKE_TRAJ_SRC:-$(ls -d "$REAL"/outputs/loop3/_quarantine/*/rep1-i1/benchmarks 2>/dev/null | head -1)}"

[[ -n "$FAKE_TRAJ_SRC" && -d "$FAKE_TRAJ_SRC" ]] || {
  echo "ERROR: no quarantined trajectories to replay. Set FAKE_TRAJ_SRC to a dir" >&2
  echo "       containing <tag>-r{0..N}-traj directories." >&2; exit 2; }

cleanup() {
  [[ "${SMOKE_KEEP:-0}" == "1" ]] && return 0
  rm -rf "$SMOKE"
  rm -rf "$REAL/outputs/loop3/rep${REP}" "$REAL/outputs/sft/loop3_rep${REP}"_i*
  rm -rf "$REAL"/recipe/tb2_evolver/runs/loop3-rep${REP}-i*
  rm -rf "$REAL"/recipe/tb2_evolver/fault_routing_loop3_rep${REP}_i*
  rm -rf "$REAL"/recipe/tb2_sft/data/loop3_rep${REP}_i*
  rm -rf "$REAL"/.benchmarks/tb2/loop3-rep${REP}-i*
}
trap cleanup EXIT
[[ "${SMOKE_KEEP:-0}" == "1" ]] || cleanup

# ── fake ROOT: real repo everywhere except scripts/ ──────────────────────────
mkdir -p "$SMOKE/scripts"
for p in recipe configs data benchmarks outputs logs .benchmarks harnessx; do
  [[ -e "$REAL/$p" ]] && ln -sfn "$REAL/$p" "$SMOKE/$p"
done
cp "$REAL/scripts/run_loop_step3.sh" "$SMOKE/scripts/"

ASSERT_LOG="$SMOKE/assertions.log"
: >"$ASSERT_LOG"

# ── stub: evolve ────────────────────────────────────────────────────────────
cat >"$SMOKE/scripts/evolve.sh" <<STUB
#!/usr/bin/env bash
set -Eeuo pipefail
REAL="$REAL"; SRC="$FAKE_TRAJ_SRC"; LOG="$ASSERT_LOG"
fail() { echo "STUB-ASSERT-FAIL evolve: \$*" | tee -a "\$LOG" >&2; exit 90; }

[[ -n "\${RUN_TAG:-}" ]]            || fail "RUN_TAG unset"
[[ "\${ROUTED_EVOLVE:-}" == "1" ]]  || fail "ROUTED_EVOLVE not 1 (routing would be off)"
[[ -n "\${SEED_HARNESS:-}" ]]       || fail "SEED_HARNESS unset — iterations would not chain"
[[ -f "\$SEED_HARNESS" ]]           || fail "SEED_HARNESS not a file: \$SEED_HARNESS"
[[ -n "\${NUM_ROUNDS:-}" ]]         || fail "NUM_ROUNDS unset"
if [[ -n "\${LORA_PATH:-}" ]]; then
  [[ -d "\$LORA_PATH" ]] || fail "LORA_PATH set but missing: \$LORA_PATH"
fi
echo "evolve tag=\$RUN_TAG seed=\$SEED_HARNESS lora=\${LORA_PATH:-<base>} rounds=\$NUM_ROUNDS" >>"\$LOG"

# Round trajectory dirs the C stage will route: symlink the replayed ones.
n=0
for d in "\$SRC"/*-r*-traj; do
  [[ -d "\$d" ]] || continue
  ln -sfn "\$d" "\$REAL/.benchmarks/tb2/\${RUN_TAG}-r\${n}-traj"
  n=\$((n+1))
done
[[ \$n -gt 0 ]] || fail "no replay trajectory dirs found under \$SRC"

# Evolve state: an R0 anchor plus a gated best_so_far pointing at a real YAML.
SD="\$REAL/recipe/tb2_evolver/runs/\$RUN_TAG/_meta_v2/_meta_scratch"
mkdir -p "\$SD" "\$REAL/recipe/tb2_evolver/runs/\$RUN_TAG/R1"
CFG="\$REAL/recipe/tb2_evolver/runs/\$RUN_TAG/R1/config.yaml"
cp "\$SEED_HARNESS" "\$CFG"
cat >"\$SD/harness_evolve_state.json" <<JSON
{"status":"completed","next_input_round":\$NUM_ROUNDS,
 "history":[{"input_round":0,"output_round":1,"score":0.4642857142857143},
            {"input_round":1,"output_round":2,"score":0.5}],
 "best_so_far":{"score":0.5,"config":"\$CFG","round":1}}
JSON
echo "  -> wrote state + \$n round dirs" >>"\$LOG"
STUB

# ── stub: evaluate ──────────────────────────────────────────────────────────
cat >"$SMOKE/scripts/evaluate.sh" <<STUB
#!/usr/bin/env bash
set -Eeuo pipefail
REAL="$REAL"; LOG="$ASSERT_LOG"
fail() { echo "STUB-ASSERT-FAIL evaluate: \$*" | tee -a "\$LOG" >&2; exit 91; }

[[ -n "\${JOB_NAME:-}" ]]        || fail "JOB_NAME unset"
[[ -n "\${HARNESS_CONFIG:-}" ]]  || fail "HARNESS_CONFIG unset"
[[ -f "\$HARNESS_CONFIG" ]]      || fail "HARNESS_CONFIG missing: \$HARNESS_CONFIG"
if [[ "\${EVAL_SFT:-0}" == "1" ]]; then
  [[ -n "\${LORA_PATH:-}" ]] || fail "EVAL_SFT=1 but LORA_PATH unset"
  [[ -d "\$LORA_PATH" ]]     || fail "adapter missing: \$LORA_PATH"
fi
echo "eval job=\$JOB_NAME harness=\$(basename \$(dirname \$HARNESS_CONFIG))/\$(basename \$HARNESS_CONFIG) sft=\${EVAL_SFT:-0} lora=\${LORA_PATH:-<base>}" >>"\$LOG"

# Fabricate a plausible result set so score_of has something to count.
D="\$REAL/.benchmarks/tb2/\$JOB_NAME"; mkdir -p "\$D"
i=0
for t in \$("\$(command -v python3)" -c "import json,sys;[print(x) for x in json.load(open(sys.argv[1]))]" "\$TASKS_JSON"); do
  mkdir -p "\$D/\${t}__trial"
  r=0; [[ \$((i % 2)) -eq 0 ]] && r=1
  echo "{\"task_name\":\"\$t\",\"verifier_result\":{\"rewards\":{\"reward\":\$r}}}" >"\$D/\${t}__trial/result.json"
  i=\$((i+1))
done
STUB

# ── stub: train_sft ─────────────────────────────────────────────────────────
cat >"$SMOKE/scripts/train_sft.sh" <<STUB
#!/usr/bin/env bash
set -Eeuo pipefail
REAL="$REAL"; LOG="$ASSERT_LOG"
fail() { echo "STUB-ASSERT-FAIL train_sft: \$*" | tee -a "\$LOG" >&2; exit 92; }

[[ -n "\${SFT_DATASET_NAME:-}" ]] || fail "SFT_DATASET_NAME unset (train_sft.sh reads this, not DATASET_NAME)"
DD="\$REAL/recipe/tb2_sft/data/\$SFT_DATASET_NAME"
[[ -d "\$DD" ]]                   || fail "corpus dir missing: \$DD"
[[ -s "\$DD/train.jsonl" ]]       || fail "corpus has no train.jsonl: \$DD"
[[ -n "\${SFT_OUTPUT_DIR:-}" ]]   || fail "SFT_OUTPUT_DIR unset"
if [[ -n "\${INIT_ADAPTER:-}" ]]; then
  [[ -d "\$INIT_ADAPTER" ]] || fail "INIT_ADAPTER missing: \$INIT_ADAPTER"
  [[ "\$(readlink -f "\$INIT_ADAPTER")" != "\$(readlink -f "\$SFT_OUTPUT_DIR")" ]] \\
    || fail "INIT_ADAPTER == SFT_OUTPUT_DIR (would overwrite weights being read)"
fi
np=\$(wc -l <"\$DD/train.jsonl")
echo "sft data=\$SFT_DATASET_NAME pairs=\$np out=\$(basename \$SFT_OUTPUT_DIR) init=\${INIT_ADAPTER:-<from base>} epochs=\${SFT_EPOCHS:-?} gpus=\${SFT_GPUS:-?}" >>"\$LOG"
mkdir -p "\$SFT_OUTPUT_DIR"; : >"\$SFT_OUTPUT_DIR/adapter_model.safetensors"
STUB

chmod +x "$SMOKE/scripts/"*.sh

# ── run ─────────────────────────────────────────────────────────────────────
echo "════════ smoke: $N_ITERS iterations, REPLICATE=$REP ════════"
rc=0
REPLICATE="$REP" N_ITERS="$N_ITERS" GPU_POOL="0,1" \
  bash "$SMOKE/scripts/run_loop_step3.sh" >"$SMOKE/run.log" 2>&1 || rc=$?

echo
echo "──── stub assertions (env each stage actually received) ────"
cat "$ASSERT_LOG"

echo
echo "──── scores.tsv ────"
column -t "$REAL/outputs/loop3/rep${REP}/scores.tsv" 2>/dev/null || true

echo
if (( rc != 0 )); then
  echo "──── FAILED (exit $rc) — last 40 log lines ────"
  tail -40 "$SMOKE/run.log"
  exit "$rc"
fi

# ── post-conditions the run itself cannot check ─────────────────────────────
echo "──── post-conditions ────"
S="$REAL/outputs/loop3/rep${REP}/scores.tsv"
py="$(command -v python3)"
"$py" - "$S" "$N_ITERS" <<'PY'
import sys
from pathlib import Path
rows = [l.rstrip("\n").split("\t") for l in Path(sys.argv[1]).read_text().splitlines()[1:]]
n = int(sys.argv[2])
bad = []
for it, stage, model, harness, passed, total, job in rows:
    if "/" in model:
        bad.append(f"model label contains a path: {model!r}")
    if not total.isdigit() or int(total) == 0:
        bad.append(f"bad denominator on {job}: {total!r}")
for k in range(1, n + 1):
    got = {r[1] for r in rows if r[0] == str(k)}
    for want in ("R0_anchor", "B_eval_harness", "E_eval_sft"):
        if want not in got:
            bad.append(f"iteration {k} missing {want}")
    exp_in = "base" if k == 1 else f"sft_i{k-1}"
    for r in rows:
        if r[0] == str(k) and r[1] == "B_eval_harness" and r[2] != exp_in:
            bad.append(f"iteration {k} B row has model={r[2]!r}, expected {exp_in!r}")
for b in bad:
    print("  FAIL:", b)
print(f"  {len(rows)} score rows, {len(bad)} problem(s)")
sys.exit(1 if bad else 0)
PY
echo
echo "✅ smoke passed — every stage wired correctly for $N_ITERS iterations"
