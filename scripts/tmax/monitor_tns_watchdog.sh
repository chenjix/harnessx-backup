#!/usr/bin/env bash
# Overnight watchdog for the 9B-base SFT-recipe tournament (rep22, job name
# tmax-coev-tns). Submits if nothing is live, logs pending reasons, and
# resubmits on cancel/preempt/fail. AssocGrpNodeLimit is not a failure.
#
# Stop:  kill $(cat /fsx/home/jixuan.chen/logs/monitor_tns.pid)
set -Eeuo pipefail

LOGDIR=/fsx/home/jixuan.chen/logs
REPO=/fsx/home/jixuan.chen/harnessx-backup
SUBMIT="$REPO/scripts/tmax/submit_tournament_sft_9b.sh"
STATE="$REPO/outputs/tmax_coevolve/rep22/STATUS"
NAME=tmax-coev-tns
HB="$LOGDIR/monitor_tns.heartbeat"
JOBFILE="$LOGDIR/monitor_tns.jobid"
PIDFILE="$LOGDIR/monitor_tns.pid"
LOCK="$LOGDIR/monitor_tns.lock"
MAX_RESUBMIT="${MAX_RESUBMIT:-25}"
SLEEP_S="${SLEEP_S:-45}"

mkdir -p "$LOGDIR"
exec 8>"$LOCK"
if ! flock -n 8; then
  echo "[mon] another watchdog already holds $LOCK" | tee -a "$HB"
  exit 0
fi
echo $$ >"$PIDFILE"
n_resub=0
echo "[mon] start pid=$$ $(date -u '+%F %T UTC')" | tee -a "$HB"

log() { echo "[mon] $(date -u '+%F %T UTC') $*" | tee -a "$HB"; }

sq() { timeout 25 squeue "$@" 2>/dev/null; }

live_line() {
  local out rc=0
  out=$(sq -u jixuan.chen -n "$NAME" -h -o '%i %T %R') || rc=$?
  if (( rc == 124 )); then
    echo "__SQ_TIMEOUT__"
    return 0
  fi
  awk 'NF{print; exit}' <<<"$out"
}

job_state() {
  local jid="$1"
  timeout 25 sacct -j "$jid" -n -X -P -o State,ExitCode,Reason 2>/dev/null \
    | awk -F'|' 'NF{print; exit}'
}

status_done() {
  [[ -f "$STATE" ]] && grep -q '^DONE' "$STATE"
}

submit_now() {
  local why="$1"
  if (( n_resub >= MAX_RESUBMIT )); then
    log "ERROR: hit MAX_RESUBMIT=$MAX_RESUBMIT; not submitting ($why)"
    exit 3
  fi
  unset INIT_LORA_PATH EVOLVE_EXTRA_ARGS MIN_IMAGE_GB PER_TASK MAX_PAIRS_PER_TRAJ \
        CORPUS_WINNER_ONLY CORPUS_MAX_PREV_FRAC CORPUS_EVAL_HARNESS MODEL_SIZE \
        TMAX_CONCURRENT HOLDOUT_CONCURRENT || true
  log "SUBMIT ($why) n_resub=$n_resub"
  local out
  out=$(bash "$SUBMIT")
  log "$out"
  local jid
  jid=$(grep -oE '[0-9]+' <<<"$out" | tail -1)
  if [[ -z "$jid" ]]; then
    log "ERROR: sbatch produced no job id: $out"
    return 1
  fi
  echo "$jid" >"$JOBFILE"
  n_resub=$((n_resub + 1))
  sleep 8
}

# First action: if no live tns job, submit (queues behind 3115 if that is still R).
_first=$(live_line)
if [[ "$_first" == "__SQ_TIMEOUT__" ]]; then
  log "squeue timeout on start — defer submit to the loop"
elif [[ -z "$_first" ]]; then
  if status_done; then
    log "rep22 STATUS is DONE — nothing to do"
    exit 0
  fi
  submit_now "initial"
else
  log "already live: $_first"
  awk '{print $1}' <<<"$_first" >"$JOBFILE"
fi

while true; do
  line=$(live_line)
  if [[ "$line" == "__SQ_TIMEOUT__" ]]; then
    log "squeue timeout — keep waiting (do not resubmit)"
    sleep "$SLEEP_S"
    continue
  fi
  if [[ -n "$line" ]]; then
    jid=$(awk '{print $1}' <<<"$line")
    st=$(awk '{print $2}' <<<"$line")
    reason=$(awk '{ $1=""; $2=""; sub(/^  */,""); print }' <<<"$line")
    echo "$jid" >"$JOBFILE"
    if [[ "$reason" == *AssocGrpNodeLimit* ]]; then
      log "job=$jid $st reason=$reason  (AssocGrpNodeLimit is not a real block; keep waiting)"
    elif [[ "$st" == "PD" ]]; then
      log "job=$jid PENDING reason=$reason  (not resubmitting; waiting for a node)"
    else
      stage="?"
      [[ -f "$STATE" ]] && stage=$(tr '\n' ' ' <"$STATE")
      log "job=$jid $st node=$reason  status=$stage"
    fi
    sleep "$SLEEP_S"
    continue
  fi

  if status_done; then
    log "rep22 STATUS=DONE — watchdog exiting"
    exit 0
  fi

  jid=""
  [[ -f "$JOBFILE" ]] && jid=$(cat "$JOBFILE")
  acct="no-jobfile"
  if [[ -n "$jid" ]]; then
    acct=$(job_state "$jid")
    [[ -z "$acct" ]] && acct="missing"
  fi
  state=$(cut -d'|' -f1 <<<"$acct")
  exitc=$(cut -d'|' -f2 <<<"$acct")
  reason=$(cut -d'|' -f3- <<<"$acct")
  log "not in queue job=${jid:-none} sacct=$acct"

  case "${state^^}" in
    COMPLETED)
      if [[ "${exitc%%:*}" == "0" ]]; then
        log "COMPLETED exit=0 — done"
        exit 0
      fi
      log "COMPLETED with nonzero exit=$exitc reason=$reason — resubmit to resume"
      submit_now "completed-nonzero:$jid"
      ;;
    CANCELLED*|PREEMPTED*|FAILED*|NODE_FAIL*|TIMEOUT*|OUT_OF_MEMORY*|BOOT_FAIL*)
      log "terminal state=$state reason=$reason — resubmit"
      submit_now "terminal:$state:$jid"
      ;;
    ""|"NO-JOBFILE"|"MISSING")
      log "no live job and no usable sacct — submit"
      submit_now "missing"
      ;;
    *)
      # COMPLETING / REQUEUED / etc. that already left squeue — wait one more tick
      log "ambiguous state=$state — wait then re-check"
      sleep "$SLEEP_S"
      continue
      ;;
  esac
  sleep "$SLEEP_S"
done
