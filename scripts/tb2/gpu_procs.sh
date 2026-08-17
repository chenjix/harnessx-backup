#!/usr/bin/env bash
set -uo pipefail
# Show what every GPU-resident process is actually running.
#
# nvidia-smi only prints a truncated process *name* ("python",
# "VLLM::EngineCore"), which cannot tell an eval's vLLM replica apart from a
# training rank or from an orphan still holding memory after its job exited.
# Everything below comes from /proc, so no extra tooling is required.
#
# Usage: bash scripts/gpu_procs.sh

if ! command -v nvidia-smi >/dev/null; then
  echo "nvidia-smi not found — not a GPU node." >&2
  exit 2
fi
if ! nvidia-smi -L >/dev/null 2>&1; then
  echo "nvidia-smi present but the driver is not responding — not a GPU node." >&2
  exit 2
fi

mapfile -t PIDS < <(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ' | grep -E '^[0-9]+$')
if ((${#PIDS[@]} == 0)); then
  echo "No compute processes on any GPU."
  exit 0
fi

_ancestor_script() {
  # Walk up the parent chain to the outermost *.sh — that is what identifies
  # which pipeline stage a process belongs to.
  local pid="$1" found="" guard=0 args
  while [[ -n "$pid" && "$pid" != "1" && $guard -lt 40 ]]; do
    args="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
    [[ "$args" == *".sh"* ]] && found="$(grep -oE '[^ ]*\.sh' <<<"$args" | head -1)"
    pid="$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)"
    ((guard++)) || true
  done
  echo "${found:-<none>}"
}

echo "=== GPU processes ==="
for pid in "${PIDS[@]}"; do
  if [[ ! -d "/proc/$pid" ]]; then
    echo "── PID $pid — already exited (stale nvidia-smi entry)"
    continue
  fi
  mem="$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null \
          | awk -F, -v p="$pid" '{gsub(/ /,"",$1); if ($1==p) {gsub(/^ /,"",$2); print $2; exit}}')"
  echo "── PID $pid   mem=${mem:-?}   up=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')"
  echo "   cmd : $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-160)"
  echo "   cwd : $(readlink -f "/proc/$pid/cwd" 2>/dev/null)"
  echo "   ppid: $(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)   stage: $(_ancestor_script "$pid")"
done

echo
echo "=== grouped by owning stage ==="
for pid in "${PIDS[@]}"; do
  [[ -d "/proc/$pid" ]] && _ancestor_script "$pid"
done | sort | uniq -c | sort -rn | sed 's/^/  /'

echo
echo "=== orphan check: GPU processes whose ancestor script is gone ==="
found_orphan=0
for pid in "${PIDS[@]}"; do
  [[ -d "/proc/$pid" ]] || continue
  if [[ "$(_ancestor_script "$pid")" == "<none>" ]]; then
    echo "  PID $pid  $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-100)"
    found_orphan=1
  fi
done
((found_orphan)) || echo "  none — every GPU process still belongs to a live launcher script"
