#!/usr/bin/env bash
set -Eeuo pipefail
# Watch pending Slurm jobs and launch a loop3 chain on each node as it lands.
#
# Run this on the login node, detached. It polls `squeue`, and the moment a
# watched job goes RUNNING it ssh-es to the allocated node and starts the chain
# in tmux. Nothing is launched twice: each job records a marker once dispatched.
#
# Three things it handles that a bare ssh would get wrong:
#
#  * GPU selection. gpu_subm.sbatch requests 4 GPUs and pins them at 99% util
#    with busy-loop matmuls to hold the allocation. A chain launched on the
#    default 0-7 pool puts half its vLLM replicas on saturated cards. Those
#    holders must NOT be killed — the sbatch ends in `wait`, so killing them
#    ends the job and releases the node. So the pool is derived from which GPUs
#    are actually idle.
#  * Disk. The image-pull filesystem filling up is what invalidated the previous
#    three chains; a node that starts below the floor is skipped with a loud
#    message instead of producing a run that has to be thrown away.
#  * Environment. The chain is started under ~/.venv, which is the interpreter
#    that has pandas/pyarrow — the base conda python does not, and the corpus
#    stage dies on it after the evolve has already been paid for.
#
# Usage:
#   bash scripts/await_and_launch.sh 27702:1 27703:2
#       -> job 27702 becomes REPLICATE=1, job 27703 becomes REPLICATE=2
#
#   bash scripts/await_and_launch.sh --dry-run 27702:1     # print, do not launch
#
# Env:
#   POLL_SECONDS=60     how often to check squeue
#   MIN_FREE_GB=40      refuse to launch below this on the root filesystem
#   MIN_GPUS=4          refuse to launch with fewer idle GPUs than this
#   REMOTE_REPO=~/qwen35-tb2-fullstack

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
POLL_SECONDS="${POLL_SECONDS:-60}"
MIN_FREE_GB="${MIN_FREE_GB:-40}"
MIN_GPUS="${MIN_GPUS:-4}"
REMOTE_REPO="${REMOTE_REPO:-\$HOME/qwen35-tb2-fullstack}"
STATE_DIR="$ROOT/outputs/loop3/_dispatch"
mkdir -p "$STATE_DIR"

DRY=0
SPECS=()
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    *:*) SPECS+=("$a") ;;
    *) echo "ERROR: expected <jobid>:<replicate>, got '$a'" >&2; exit 2 ;;
  esac
done
[[ ${#SPECS[@]} -gt 0 ]] || { echo "usage: await_and_launch.sh [--dry-run] <jobid>:<rep> ..." >&2; exit 2; }

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=15"

node_of() {  # node_of <jobid> -> hostname, empty if not running
  squeue -j "$1" -h -o "%T %N" 2>/dev/null | awk '$1=="RUNNING"{print $2}'
}

# Idle GPUs = those with no compute process holding memory. The holder sits at
# ~845 MiB per card, so a plain "is it idle" threshold separates them cleanly
# from genuinely free cards without needing to parse process tables.
probe_node() {  # probe_node <host> -> "<gpu_pool>|<free_gb>" or "" on failure
  $SSH "$1" 'bash -s' <<'REMOTE' 2>/dev/null
pool=$(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits 2>/dev/null \
       | awk -F', *' '$2 < 200 {printf "%s%s", sep, $1; sep=","}')
free=$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')
printf '%s|%s\n' "$pool" "$free"
REMOTE
}

launch() {  # launch <host> <replicate>
  local host="$1" rep="$2" pool="$3"
  local cmd
  # shellcheck disable=SC2016  # $HOME etc must expand on the remote, not here
  cmd=$(cat <<REMOTE
set -Eeuo pipefail
cd $REMOTE_REPO
source \$HOME/.venv/bin/activate
export PYTHON_BIN="\$HOME/.venv/bin/python"
tmux kill-session -t loop3 2>/dev/null || true
tmux new -s loop3 -d "source \\\$HOME/.venv/bin/activate && \
CLEAN_STALE=1 \
PYTHON_BIN=\\\$HOME/.venv/bin/python \
GPU_POOL='$pool' \
TB2_CONCURRENT=1 \
TB2_MIN_FREE_GB=$MIN_FREE_GB \
REPLICATE=$rep \
bash scripts/run_loop_step3.sh 2>&1 | tee loop3-rep$rep.log"
sleep 3
tmux ls
REMOTE
)
  if (( DRY )); then
    log "DRY-RUN would run on $host (rep$rep, gpus=$pool):"
    printf '%s\n' "$cmd" | sed 's/^/    /'
    return 0
  fi
  $SSH "$host" "$cmd"
}

log "watching: ${SPECS[*]}   poll=${POLL_SECONDS}s  min_free=${MIN_FREE_GB}G  min_gpus=${MIN_GPUS}"

remaining=${#SPECS[@]}
while (( remaining > 0 )); do
  for spec in "${SPECS[@]}"; do
    job="${spec%%:*}"; rep="${spec##*:}"
    marker="$STATE_DIR/.launched-$job"
    [[ -f "$marker" ]] && continue

    host="$(node_of "$job")"
    if [[ -z "$host" ]]; then
      continue
    fi

    log "job $job is RUNNING on $host — probing"
    probe="$(probe_node "$host" || true)"
    if [[ -z "$probe" ]]; then
      log "  cannot reach $host over ssh; will retry"
      continue
    fi
    pool="${probe%%|*}"; free="${probe##*|}"
    n_gpu=0; [[ -n "$pool" ]] && n_gpu=$(awk -F',' '{print NF}' <<<"$pool")

    log "  idle GPUs: ${pool:-none} (n=$n_gpu)   root free: ${free:-?}G"

    if (( n_gpu < MIN_GPUS )); then
      log "  SKIP: only $n_gpu idle GPU(s); the allocation's hold processes are on the rest."
      log "        Do not kill them — gpu_subm.sbatch ends in \`wait\`, so that releases the node."
      log "        Either accept fewer GPUs (MIN_GPUS=$n_gpu) or resubmit with --gpus-per-node=8."
      continue
    fi
    if [[ -n "$free" ]] && (( free < MIN_FREE_GB )); then
      log "  SKIP: only ${free}G free on / (need ${MIN_FREE_GB}G). Image pulls stream through"
      log "        this filesystem; starting here reproduces the run that scored 0/28."
      log "        Free space on $host, then this watcher will pick it up on the next poll."
      continue
    fi

    log "  launching REPLICATE=$rep on $host with GPU_POOL=$pool"
    if launch "$host" "$rep" "$pool"; then
      (( DRY )) || { printf '%s\t%s\t%s\t%s\n' "$job" "$host" "$rep" "$pool" >"$marker"; }
      log "  started (attach: ssh $host -t tmux attach -t loop3)"
      (( DRY )) || remaining=$(( remaining - 1 ))
    else
      log "  launch failed on $host; will retry next poll"
    fi
  done
  (( DRY )) && break
  (( remaining > 0 )) && sleep "$POLL_SECONDS"
done

log "all watched jobs dispatched"
column -t "$STATE_DIR"/.launched-* 2>/dev/null || true
