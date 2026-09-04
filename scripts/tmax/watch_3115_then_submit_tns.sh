#!/usr/bin/env bash
# Wait until Slurm job 3115 (4B tournament, rep21) leaves the queue, then
# submit the 9B-base SFT-recipe tournament (rep22). Idempotent: a second
# watcher that sees the stamp file will exit without submitting again.
set -Eeuo pipefail

WATCH_JOB="${WATCH_JOB:-3115}"
LOGDIR=/fsx/home/jixuan.chen/logs
HB="$LOGDIR/watch_${WATCH_JOB}_then_tns.heartbeat"
STAMP="$LOGDIR/watch_${WATCH_JOB}_then_tns.submitted"
SUBMIT=/fsx/home/jixuan.chen/harnessx-backup/scripts/tmax/submit_tournament_sft_9b.sh
SLEEP_S="${SLEEP_S:-30}"

mkdir -p "$LOGDIR"
echo "[watch] start pid=$$ job=$WATCH_JOB $(date -u '+%F %T UTC')" | tee -a "$HB"

if [[ -f "$STAMP" ]]; then
  echo "[watch] already submitted: $(cat "$STAMP")" | tee -a "$HB"
  exit 0
fi

while true; do
  st=$(timeout 20 squeue -j "$WATCH_JOB" -h -o '%T %M %R' 2>/dev/null || echo TIMEOUT)
  ts=$(date -u '+%F %T UTC')
  if [[ "$st" == "TIMEOUT" ]]; then
    echo "[watch] $ts $WATCH_JOB squeue-timeout — still waiting" | tee -a "$HB"
    sleep "$SLEEP_S"
    continue
  fi
  if [[ -z "${st// }" ]]; then
    echo "[watch] $ts $WATCH_JOB GONE — submitting 9b SFT tournament" | tee -a "$HB"
    break
  fi
  echo "[watch] $ts $WATCH_JOB $st" | tee -a "$HB"
  sleep "$SLEEP_S"
done

if [[ -f "$STAMP" ]]; then
  echo "[watch] stamp appeared while waiting; skip submit: $(cat "$STAMP")" | tee -a "$HB"
  exit 0
fi

out=$(bash "$SUBMIT")
echo "$out" | tee -a "$HB"
echo "$out" >"$STAMP"
echo "[watch] done $(date -u '+%F %T UTC')" | tee -a "$HB"
