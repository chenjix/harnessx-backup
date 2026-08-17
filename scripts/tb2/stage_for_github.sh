#!/usr/bin/env bash
set -Eeuo pipefail
# Build a clean, secret-scanned git staging area for the backup repo.
#
# Whitelist, not blacklist. The working tree carries evolve run directories,
# trajectory dumps, adapters and parquet corpora that dwarf the source by orders
# of magnitude and must never reach the remote; a blacklist that misses one of
# them produces a push that either fails on size or leaks data. So only source
# extensions under named source directories are copied, and everything else is
# absent by construction rather than by exclusion.
#
# The scan at the end is a hard gate, not a warning: the SFG API key lives in
# .env in this tree, and a key that reaches a remote is compromised whether or
# not the repo is private and whether or not the commit is later reverted.
#
# Usage: bash scripts/stage_for_github.sh [remote-url]

# Resolve scripts/ whether this file lives in scripts/, scripts/tb2/, or scripts/tmax/
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"
STAGE="$ROOT/.gh_backup_stage"
REMOTE="${1:-git@github.com:chenjix/harnessx-backup.git}"

SRC_DIRS=(scripts recipe harnessx benchmarks configs extensions)
EXTS=(py sh yaml yml json md j2 jinja toml cfg ini txt)
# Directories that hold outputs rather than code, at any depth.
PRUNE=(runs data outputs logs .benchmarks checkpoints __pycache__ .git node_modules
       .venv .smoke_root _quarantine _dispatch data-gym-cache reference)

echo "staging → $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE"

prune_expr=()
for d in "${PRUNE[@]}"; do prune_expr+=(-name "$d" -o); done
unset 'prune_expr[${#prune_expr[@]}-1]'

name_expr=()
for e in "${EXTS[@]}"; do name_expr+=(-name "*.$e" -o); done
unset 'name_expr[${#name_expr[@]}-1]'

n=0
for d in "${SRC_DIRS[@]}"; do
  [[ -d "$ROOT/$d" ]] || continue
  while IFS= read -r -d '' f; do
    rel="${f#$ROOT/}"
    mkdir -p "$STAGE/$(dirname "$rel")"
    cp "$f" "$STAGE/$rel"
    n=$((n + 1))
  done < <(find "$ROOT/$d" \( "${prune_expr[@]}" \) -prune -o -type f \( "${name_expr[@]}" \) -print0)
done
echo "  copied $n source file(s)"

# Small top-level files worth carrying, if present.
for f in README.md pyproject.toml requirements.txt setup.py Makefile .env.example; do
  [[ -f "$ROOT/$f" ]] && cp "$ROOT/$f" "$STAGE/$f" && echo "  + $f"
done

cat >"$STAGE/.gitignore" <<'EOF'
# Secrets — never commit. The SFG API key lives here in the working tree.
.env
*.key
*.pem
credentials*

# Outputs, not source
outputs/
logs/
.benchmarks/
checkpoints/
data/
recipe/*/runs/
recipe/*/fault_routing*/
recipe/tb2_sft/data/
*.parquet
*.jsonl
*.safetensors
*.bin

# Python
__pycache__/
*.pyc
.venv/
EOF

# ── secret scan: hard gate ──────────────────────────────────────────────────
echo
echo "scanning staged files for secrets..."
PATTERNS=(
  'SFG_API_KEY[[:space:]]*=[[:space:]]*[A-Za-z0-9]'
  'OPENAI_API_KEY[[:space:]]*=[[:space:]]*[A-Za-z0-9]'
  'sk-[A-Za-z0-9]\{20,\}'
  'ghp_[A-Za-z0-9]\{30,\}'
  'AKIA[0-9A-Z]\{16\}'
  '-----BEGIN[[:space:]].*PRIVATE KEY-----'
)
hits=0

# Scan for the ACTUAL secret values this tree holds, read from .env at runtime.
# Embedding them in this script would put the key into source control — which is
# exactly what the gate exists to prevent, and it is how the first run of this
# script failed: the scanner matched itself after being copied into the staging
# area. Values are used but never printed.
if [[ -f "$ROOT/.env" ]]; then
  while IFS='=' read -r k v; do
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    [[ "$k" =~ (KEY|TOKEN|SECRET|PASSWORD) ]] || continue
    [[ ${#v} -ge 16 ]] || continue
    if out="$(grep -rIln --binary-files=without-match -F "$v" "$STAGE" 2>/dev/null)"; then
      echo "  !! a value of \$$k from .env appears in:" >&2
      printf '%s\n' "$out" | head -5 | sed 's/^/     /' >&2
      hits=$((hits + 1))
    fi
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ROOT/.env" 2>/dev/null || true)
fi

for p in "${PATTERNS[@]}"; do
  if out="$(grep -rIn --binary-files=without-match "$p" "$STAGE" 2>/dev/null)"; then
    echo "  !! MATCH for /$p/:" >&2
    printf '%s\n' "$out" | head -5 | sed 's/^/     /' >&2
    hits=$((hits + 1))
  fi
done
if [[ -e "$STAGE/.env" ]]; then
  echo "  !! .env present in staging area" >&2
  hits=$((hits + 1))
fi
if (( hits )); then
  echo >&2
  echo "ABORTED: $hits secret pattern(s) matched. Nothing was committed." >&2
  echo "Remove the offending content, then re-run. Do not commit and revert —" >&2
  echo "a key that reaches a remote is compromised even if the commit is undone." >&2
  exit 2
fi
echo "  clean — no secret patterns matched, no .env staged"

# ── commit ──────────────────────────────────────────────────────────────────
cd "$STAGE"
git init -q
git add -A
git -c user.email="jic182@ucsd.edu" -c user.name="jixuan.chen" commit -q -m "$(cat <<'MSG'
Routed harness<->model co-evolution loop: fixes from the first live runs

Pipeline
- run_loop_step3.sh: 3-iteration harness->SFT loop, each iteration seeded by the
  previous iteration's gated harness and continued adapter; per-replicate lock;
  status file and ERR banner; disk guards; greedy decoding for all rollouts.
- report_coevolve.sh: reads the chain stage by stage, using the R0_anchor repeat
  of the previous iteration's condition as an in-chain noise estimate.
- smoke_loop3.sh: stubbed end-to-end test of all three iterations, minutes, no GPU.
- await_and_launch.sh: dispatches a chain onto a Slurm node as it is allocated.
- quarantine_loop3_iter.sh: move one iteration's artifacts aside for a clean re-run.

Routing
- routed_evidence.py: per-round split into harness-actionable / SFT / excluded,
  cross-trajectory dedup by defect signature, and affordance gaps derived by
  contrast against Tmax recoveries.
- tmax_recovery_index.py: mines ~16k error->recovery demonstrations from the Tmax
  success corpus, indexed by error class.

Defects fixed (each found in a real run, not review)
- Gate compared against a single lucky draw; one identical config scored 17/14/11
  and the first measurement then rejected the config against itself. Now compares
  against the mean of every measurement.
- The journal's gating_outcome was never written, so the meta-agent's lever
  scoreboard was permanently empty and it could not learn from its own edits.
- history/OVERVIEW.md, per-task results and config diffs were promised to the
  meta-agent by TASK.md but never produced.
- The final round's edit was promoted without ever being scored.
- A seeded harness referenced processor files inside the previous iteration's
  directory; archiving that iteration broke the next one. Now materialized as a
  self-contained bundle.
- Evidence packs were capped at 3 defect groups while 7-10 were available, and
  the ranking is stable, so four consecutive rounds showed the same three items
  and the meta-agent stopped editing.
- Corpus stage resolved `python3` to an interpreter without pandas.
- Tmax top-up quota had a per-task floor of 1 that overrode the requested total,
  and was counted in trajectories while the gradient sees pairs (own share 0.36
  -> 0.51).
- Low disk aborted the run; it now throttles claims and resumes as running tasks
  release their images.
MSG
)"

echo
git --no-pager log --stat --oneline -1 | tail -5
echo
echo "files: $(git ls-files | wc -l)   size: $(du -sh "$STAGE/.git" 2>/dev/null | cut -f1)"
echo
echo "To push (needs your GitHub credentials):"
echo "    cd $STAGE"
echo "    git remote add origin $REMOTE"
echo "    git push -u origin main --force   # or: master, per the remote's default"
