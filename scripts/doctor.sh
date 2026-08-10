#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_common.sh"

fail=0
check_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    printf 'OK   %-18s %s\n' "$1" "$(command -v "$1")"
  else
    printf 'MISS %-18s\n' "$1"
    fail=1
  fi
}

echo "Project: $ROOT"
echo "Profile: $MODEL_SIZE ($MODEL)"
for cmd in python3 docker curl git nvidia-smi; do check_cmd "$cmd"; done

PY="$(python_bin)"
echo "Python:  $PY"
"$PY" - <<'PY' || fail=1
mods = ["openai", "litellm", "hydra", "yaml", "docker"]
for name in mods:
    try:
        __import__(name)
        print(f"OK   python module     {name}")
    except Exception as exc:
        print(f"MISS python module     {name}: {exc}")
PY

if [[ -S /var/run/docker.sock ]] && docker info >/dev/null 2>&1; then
  echo "OK   Docker daemon"
else
  echo "MISS Docker daemon/socket"
  fail=1
fi

if [[ -n "${OPENAI_API_KEY:-${SFG_API_KEY:-}}" ]]; then
  echo "OK   meta-agent API key present (value hidden)"
else
  echo "MISS OPENAI_API_KEY or SFG_API_KEY (required for evolve)"
fi

if command -v squeue >/dev/null 2>&1; then
  echo
  echo "Active Slurm jobs:"
  squeue --me || true
fi

echo
echo "SFT environment:"
if [[ -f "${CONDA_SH:-/fsx/home/jixuan.chen/miniconda3/etc/profile.d/conda.sh}" ]]; then
  echo "OK   conda initialization"
else
  echo "MISS conda initialization"
fi

echo
echo "Replay-GRPO environment:"
for var in SLIME_ROOT MEGATRON_ROOT DATA_ROOT; do
  [[ -n "${!var:-}" ]] && echo "OK   $var=${!var}" || echo "MISS $var"
done
echo "NOTE offline replay-GRPO is opt-in and is not Harbor-verifier-backed."

exit "$fail"
