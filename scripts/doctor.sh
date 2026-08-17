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

# What counts as "meta-agent auth is present" depends on the backend. A
# bedrock/* meta-model signs with SigV4 from the ambient credential chain and
# has no API key at all, so the unconditional key check reported MISS for a
# setup that works — the one kind of false alarm that trains you to ignore the
# doctor.
case "${META_MODEL:-openai/gpt-5.5}" in
  bedrock/*)
    if "$(python_bin)" -c "import botocore.session,sys; sys.exit(0 if botocore.session.get_session().get_credentials() else 1)" 2>/dev/null; then
      echo "OK   meta-agent AWS credentials resolve (${META_MODEL}, SigV4 — no API key)"
    else
      echo "MISS AWS credentials for ${META_MODEL} (Bedrock uses SigV4, not a key)"
      fail=1
    fi
    ;;
  anthropic/*)
    if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
      echo "OK   ANTHROPIC_API_KEY present (value hidden)"
    else
      echo "MISS ANTHROPIC_API_KEY (required for ${META_MODEL})"
    fi
    ;;
  *)
    if [[ -n "${OPENAI_API_KEY:-${SFG_API_KEY:-}}" ]]; then
      echo "OK   meta-agent API key present (value hidden)"
    else
      echo "MISS OPENAI_API_KEY or SFG_API_KEY (required for evolve)"
    fi
    ;;
esac

if command -v squeue >/dev/null 2>&1; then
  echo
  echo "Active Slurm jobs:"
  squeue --me || true
fi

echo
echo "SFT environment:"
if [[ -n "${SFT_PYTHON:-}" ]]; then
  # SFT_PYTHON bypasses conda entirely (train_sft.sh), so conda's absence is
  # not a finding — what matters is that this interpreter has the trainer deps.
  if [[ -x "$SFT_PYTHON" ]]; then
    sft_missing="$("$SFT_PYTHON" - <<'PY'
import importlib
missing = []
for m in ("trl", "peft", "datasets", "transformers", "torch"):
    try:
        importlib.import_module(m)
    except Exception:
        missing.append(m)
print(" ".join(missing))
PY
)"
    if [[ -z "$sft_missing" ]]; then
      echo "OK   SFT_PYTHON $SFT_PYTHON (trl/peft/datasets/transformers/torch)"
    else
      echo "MISS SFT deps in SFT_PYTHON ($SFT_PYTHON): $sft_missing"
    fi
  else
    echo "MISS SFT_PYTHON is not executable: $SFT_PYTHON"
  fi
elif [[ -f "${CONDA_SH:-/fsx/home/jixuan.chen/miniconda3/etc/profile.d/conda.sh}" ]]; then
  echo "OK   conda initialization"
else
  echo "MISS conda initialization (and SFT_PYTHON unset)"
fi

echo
echo "Replay-GRPO environment:"
for var in SLIME_ROOT MEGATRON_ROOT DATA_ROOT; do
  [[ -n "${!var:-}" ]] && echo "OK   $var=${!var}" || echo "MISS $var"
done
echo "NOTE offline replay-GRPO is opt-in and is not Harbor-verifier-backed."

exit "$fail"
