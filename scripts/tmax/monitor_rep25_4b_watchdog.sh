#!/usr/bin/env bash
# Keep the resumable rep25 4B tournament + SFT-gen chain attached to Slurm.
# Stop manually with: kill "$(cat /fsx/home/jixuan.chen/logs/monitor_rep25_4b.pid)"
set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
LOGDIR=/fsx/home/jixuan.chen/logs
SBATCH="$REPO/scripts/slurm/tmax/h200_tmax_coevolve_tournament_4b_sft.sbatch"
STATE="$REPO/outputs/tmax_coevolve/rep25/STATUS"
HB="$LOGDIR/monitor_rep25_4b.heartbeat"
JOBFILE="$LOGDIR/monitor_rep25_4b.jobid"
PIDFILE="$LOGDIR/monitor_rep25_4b.pid"
LOCK="$LOGDIR/monitor_rep25_4b.lock"
SLEEP_S="${SLEEP_S:-45}"
MAX_RESUBMIT="${MAX_RESUBMIT:-25}"

mkdir -p "$LOGDIR"
exec 8>"$LOCK"
if ! flock -n 8; then
  echo "[mon] another watchdog already holds $LOCK" | tee -a "$HB"
  exit 0
fi
echo $$ >"$PIDFILE"
n_resub=0

log() { echo "[mon] $(date -u '+%F %T UTC') $*" | tee -a "$HB"; }
status_done() { [[ -f "$STATE" ]] && grep -q '^DONE' "$STATE"; }

submit_now() {
  local why="$1" out jid
  if (( n_resub >= MAX_RESUBMIT )); then
    log "ERROR hit MAX_RESUBMIT=$MAX_RESUBMIT ($why)"
    exit 3
  fi
  log "SUBMIT reason=$why resubmit_count=$n_resub"
  out=$(env -u INIT_LORA_PATH -u EVOLVE_EXTRA_ARGS -u MIN_IMAGE_GB \
    -u PER_TASK -u MAX_PAIRS_PER_TRAJ -u CORPUS_WINNER_ONLY \
    -u CORPUS_MAX_PREV_FRAC -u CORPUS_EVAL_HARNESS \
    -u TMAX_CONCURRENT -u HOLDOUT_CONCURRENT -u SFT_GEN_ROLLOUT \
    -u SFT_GEN_CONCURRENT -u START_ITER \
    sbatch --export=ALL,REPLICATE=25,N_ITERS=3,ENABLE_RL=0,MODEL_SIZE=4b,INIT_LORA_PATH=,SFT_GEN_TASKS=200 \
    "$SBATCH")
  log "$out"
  jid=$(grep -oE '[0-9]+' <<<"$out" | tail -1)
  [[ -n "$jid" ]] || { log "ERROR no job id returned"; return 1; }
  echo "$jid" >"$JOBFILE"
  n_resub=$((n_resub + 1))
}

log "start pid=$$"
if [[ ! -s "$JOBFILE" ]]; then
  submit_now initial
fi

while true; do
  if status_done; then
    log "rep25 DONE; exiting"
    exit 0
  fi

  jid=$(cat "$JOBFILE")
  line=$(timeout 25 squeue -j "$jid" -h -o '%i|%T|%R' 2>/dev/null || true)
  if [[ -n "$line" ]]; then
    stage=unknown
    [[ -f "$STATE" ]] && stage=$(tr '\n' ' ' <"$STATE")
    log "job=$line status=$stage"
    sleep "$SLEEP_S"
    continue
  fi

  acct=$(timeout 25 sacct -j "$jid" -n -X -P -o State,ExitCode,Reason 2>/dev/null \
    | awk -F'|' 'NF {print; exit}' || true)
  state=$(cut -d'|' -f1 <<<"$acct")
  log "job=$jid left queue sacct=${acct:-missing}"
  case "${state^^}" in
    CANCELLED*|PREEMPTED*|FAILED*|NODE_FAIL*|TIMEOUT*|OUT_OF_MEMORY*|BOOT_FAIL*)
      submit_now "terminal:$state:$jid"
      ;;
    COMPLETED)
      if status_done; then
        log "completed and rep25 DONE; exiting"
        exit 0
      fi
      submit_now "completed-without-DONE:$jid"
      ;;
    *)
      log "ambiguous/missing accounting state; waiting one interval"
      ;;
  esac
  sleep "$SLEEP_S"
done
