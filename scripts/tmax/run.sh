#!/usr/bin/env bash
# Stable, discoverable entry point for the Tmax experiment stack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage: bash scripts/tmax/run.sh <command> [args]

Commands:
  preflight         Check the checkout, Python, Docker, data, and credentials
  check-env PROFILE Validate harness, sft, or rl dependencies
  evolve            Run harness evolution (requires RUN_TAG)
  eval              Evaluate a base model or LoRA adapter on Tmax
  sft               Train LoRA SFT (requires SFT_DATASET_NAME)
  rl                Run online GRPO/DPPO (requires RL_DATASET_NAME and RL_OUTPUT_DIR)
  coevolve          Run the complete resumable loop (requires REPLICATE)
  submit-coevolve   Submit the recommended H200 tournament + SFT job
  help              Show this message

Load local configuration first if needed:
  set -a; source configs/tmax.env; set +a

Examples:
  RUN_TAG=mentor-smoke LIMIT=2 bash scripts/tmax/run.sh evolve
  JOB_NAME=base-smoke LIMIT=2 bash scripts/tmax/run.sh eval
  SFT_DATASET_NAME=tmax_coev_rep22_i1 bash scripts/tmax/run.sh sft
  RL_DATASET_NAME=tmax_rl_train100 RL_OUTPUT_DIR=outputs/rl/mentor \
    RL_INIT_MODEL=/path/to/model bash scripts/tmax/run.sh rl
  REPLICATE=30 N_ITERS=1 bash scripts/tmax/run.sh coevolve
  REPLICATE=30 N_ITERS=3 bash scripts/tmax/run.sh submit-coevolve

Full handoff: docs/TMAX_RUNBOOK.md
EOF
}

preflight() {
  local failed=0
  check_file() {
    if [[ -e "$1" ]]; then printf 'OK   %s\n' "$1"; else printf 'MISS %s\n' "$1"; failed=1; fi
  }
  printf 'repo : %s\n' "$ROOT"
  printf 'git  : %s\n' "$(git rev-parse --short HEAD 2>/dev/null || echo unavailable)"
  command -v python3 >/dev/null && printf 'OK   python3: %s\n' "$(command -v python3)" || { echo 'MISS python3'; failed=1; }
  command -v docker >/dev/null && docker info >/dev/null 2>&1 \
    && echo 'OK   Docker daemon' || { echo 'MISS Docker CLI/daemon'; failed=1; }
  command -v sbatch >/dev/null && echo 'OK   Slurm sbatch' || echo 'WARN sbatch unavailable (local stages can still run)'
  check_file "configs/baseline_tmax_harness.yaml"
  check_file "recipe/tb2_evolver/tasks_tmax_evolve50_list.json"
  check_file "recipe/tb2_evolver/tasks_tmax_only200.json"
  check_file "data/external/tmax-taxonomy/data/train-00000-of-00001.parquet"
  check_file "recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl"
  check_file "recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl"
  if [[ -n "${AWS_PROFILE:-}${AWS_ACCESS_KEY_ID:-}" || -n "${OPENAI_API_KEY:-}${SFG_API_KEY:-}" ]]; then
    echo 'OK   meta-model credential variables present'
  else
    echo 'WARN no AWS/OpenAI credential variable detected'
  fi
  (( failed == 0 )) || {
    echo
    echo 'Preflight found required items marked MISS. See docs/TMAX_RUNBOOK.md.' >&2
    return 2
  }
}

cmd="${1:-help}"
shift || true
case "$cmd" in
  help|-h|--help) usage ;;
  preflight) preflight ;;
  check-env)
    profile="${1:?choose profile: harness, sft, or rl}"
    case "$profile" in harness|sft|rl) ;; *) echo "Unknown profile: $profile" >&2; exit 2 ;; esac
    case "$profile" in
      harness) env_python="${PYTHON_BIN:-python3}" ;;
      sft) env_python="${SFT_PYTHON:-${PYTHON_BIN:-python3}}" ;;
      rl) env_python="${RL_PYTHON:-$ROOT/tmax/training/open-instruct/.venv/bin/python}" ;;
    esac
    [[ "$env_python" == */* ]] && [[ ! -x "$env_python" ]] && {
      echo "ERROR: interpreter is not executable: $env_python" >&2; exit 2;
    }
    exec "$env_python" scripts/tmax/check_env.py "$profile"
    ;;
  evolve) : "${RUN_TAG:?RUN_TAG is required}"; exec bash scripts/tmax/evolve_tmax.sh "$@" ;;
  eval) exec bash scripts/tmax/evaluate_tmax.sh "$@" ;;
  sft) : "${SFT_DATASET_NAME:?SFT_DATASET_NAME is required}"; exec bash scripts/train_sft.sh "$@" ;;
  rl)
    : "${RL_DATASET_NAME:?RL_DATASET_NAME is required}"
    : "${RL_OUTPUT_DIR:?RL_OUTPUT_DIR is required}"
    exec bash scripts/tmax/train_rl_grpo.sh "$@"
    ;;
  coevolve) : "${REPLICATE:?REPLICATE is required}"; exec bash scripts/tmax/run_loop_tmax_coevolve.sh "$@" ;;
  submit-coevolve)
    : "${REPLICATE:?REPLICATE is required}"
    command -v sbatch >/dev/null || { echo 'ERROR: sbatch not found' >&2; exit 2; }
    exec sbatch --export=ALL scripts/slurm/tmax/h200_tmax_coevolve_tournament_sft.sbatch "$@"
    ;;
  *) echo "Unknown command: $cmd" >&2; usage >&2; exit 2 ;;
esac
