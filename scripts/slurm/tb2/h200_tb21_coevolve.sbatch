#!/bin/bash
# TB2 89-task harness <-> SFT co-evolution on Qwen3.5-9B, 8x H200.
# The task set is published as terminal-bench@2.0; there is no 2.1 in the registry.
#
# Validates the same coevolve machinery the Tmax chain runs -- both ratchets,
# the tie rule, evolve-task rotation, the corpus floor with top-ups, no traj cap
# -- on a second, independent benchmark. Control logic is shared verbatim with
# scripts/tmax/run_loop_tmax_coevolve.sh; only the backend differs.
#
# Why the numbers differ from the Tmax chain (they are not arbitrary):
#
#   89 tasks total. Stage S evaluates all of them once with the baseline harness,
#   then splits them into a 49-task evolve pool and a 40-task holdout, stratified
#   on that baseline so both halves have comparable difficulty. Measured over 200
#   simulated baselines, an unstratified cut leaves the two halves up to 35 pass-
#   rate points apart (mean 8pp); stratified it is at most 4pp (mean 1.4pp). That
#   same 89-task eval, restricted to the holdout ids, is the anchor -- so the
#   split costs one eval instead of two.
#
#   EVOLVE_N_TASKS=18 out of a 49-task pool. Rotation retires solved tasks and
#   refills from the pool's unused remainder, so the pool is consumed at roughly
#   n*(1+2*solve_rate) over three iterations. loop3 measured an 89% solve rate on
#   its evolve set; at that rate n=25 leaves iteration 3 with ~4 tasks. n=18
#   holds at >=17 tasks across solve rates from 45% to 85%.
#
#   MIN_TRAJS=30, not the Tmax chain's 80. loop3 yielded ~2.5 kept trajectories
#   per solved task, so an 18-task set produces roughly 30-40 -- 80 is physically
#   unreachable here and would fire top-ups every iteration forever. The corpus
#   is cumulative across iterations, so only iteration 1 sits near the floor.
#
# Runtime, extrapolated from loop3 (27b, 28 tasks) and the live 9b Tmax chain:
# ~2h for the 89-task scan, then ~5h per iteration (evolve ~2.5h, two 40-task
# holdout evals ~1.2h each, SFT ~0.3h). Three iterations land near 17h clean,
# ~20h with one top-up each, ~24h if an iteration also spends an SFT retry.
# The 48h wall is deliberate headroom: every stage writes a .done marker under
# outputs/tb21_coevolve/rep<N>/, so re-submitting resumes instead of restarting.
#
# Submit:
#   sbatch --export=ALL,REPLICATE=1,N_ITERS=3 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tb2/h200_tb21_coevolve.sbatch
#
# Resume after a timeout or preemption (identical command):
#   sbatch --export=ALL,REPLICATE=1,N_ITERS=3 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tb2/h200_tb21_coevolve.sbatch
#
# Shape check before committing the full slot (~4h: pays the 89-task scan plus
# one 2-round iteration):
#   sbatch --export=ALL,REPLICATE=9,N_ITERS=1,EVOLVE_ROUNDS=2,MAX_SFT_RETRIES=0,MAX_TOPUP_ROUNDS=0 \
#     /fsx/home/jixuan.chen/harnessx-backup/scripts/slurm/tb2/h200_tb21_coevolve.sbatch
#
#SBATCH --job-name=tb21-coev
#SBATCH --account=interactive-ai
#SBATCH --partition=ml.p5en.48xlarge
#SBATCH --nodes=1
#SBATCH --gres=gpu:h200:8
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --time=2-00:00:00
#SBATCH --requeue
#SBATCH --output=/fsx/home/jixuan.chen/logs/tb21_coev_%j.out
#SBATCH --error=/fsx/home/jixuan.chen/logs/tb21_coev_%j.err

set -Eeuo pipefail

REPO=/fsx/home/jixuan.chen/harnessx-backup
cd "$REPO"

echo "===== job ====="
echo "job     : ${SLURM_JOB_ID}  restart #${SLURM_RESTART_COUNT:-0}"
echo "node    : $(hostname)"
echo "gpus    : ${CUDA_VISIBLE_DEVICES:-unset}"
echo "start   : $(date -u '+%F %T UTC')"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

export PYTHON_BIN="${PYTHON_BIN:-/fsx/home/jixuan.chen/.venv/bin/python}"
export SFT_PYTHON="${SFT_PYTHON:-$PYTHON_BIN}"
export VLLM_VENV="${VLLM_VENV:-/fsx/home/jixuan.chen/.venv}"
# The TB2 eval path shells out through eval_local_docker.sh into harbor, and
# parts of that chain assume an activated venv. A login shell has one; a Slurm
# job does not, so put the venv first on PATH rather than relying on every
# script to honour PYTHON_BIN.
export PATH="$VLLM_VENV/bin:$PATH"
export META_MODEL="${META_MODEL:-bedrock/us.anthropic.claude-opus-4-8}"
export AWS_REGION_NAME="${AWS_REGION_NAME:-us-west-2}"
export GPU_POOL="${GPU_POOL:-0,1,2,3,4,5,6,7}"
export CLEAN_STALE="${CLEAN_STALE:-1}"

export MODEL_SIZE="${MODEL_SIZE:-9b}"
export REPLICATE="${REPLICATE:-1}"
export N_ITERS="${N_ITERS:-3}"
export EVOLVE_ROUNDS="${EVOLVE_ROUNDS:-5}"
export EXTRA_EVOLVE_ROUNDS="${EXTRA_EVOLVE_ROUNDS:-2}"
export MAX_SFT_RETRIES="${MAX_SFT_RETRIES:-2}"
export MIN_TRAJS="${MIN_TRAJS:-30}"
export MAX_TOPUP_ROUNDS="${MAX_TOPUP_ROUNDS:-3}"
export MAX_TRAJS="${MAX_TRAJS:-0}"
export PER_TASK="${PER_TASK:-3}"
export SFT_EPOCHS="${SFT_EPOCHS:-2}"
export CUMULATIVE_CORPUS="${CUMULATIVE_CORPUS:-1}"

# ── the 89-task split ───────────────────────────────────────────────────────
export N_HOLDOUT="${N_HOLDOUT:-40}"
export SPLIT_SEED="${SPLIT_SEED:-42}"
export EVOLVE_N_TASKS="${EVOLVE_N_TASKS:-18}"
export EVOLVE_TASK_SEED="${EVOLVE_TASK_SEED:-42}"
export ROTATE_EVOLVE_TASKS="${ROTATE_EVOLVE_TASKS:-1}"

# ── ratchet: keep the floor rising, but let genuine ties through ────────────
export RATCHET_ALLOW_TIE="${RATCHET_ALLOW_TIE:-1}"
export SYSERR_TOL="${SYSERR_TOL:-0}"
export TIE_MAX_SYSERR="${TIE_MAX_SYSERR:-0}"
export MODEL_RATCHET_TOL="${MODEL_RATCHET_TOL:-0}"
export HARNESS_RATCHET="${HARNESS_RATCHET:-1}"

# ── TB2 backend ─────────────────────────────────────────────────────────────
# The 89-task set is published as terminal-bench@2.0 (sourced from the
# terminal-bench-2 repo); the registry has no 2.1, so naming one fails every task.
export TB2_DATASET="${TB2_DATASET:-terminal-bench@2.0}"
export TB2_MAX_STEPS="${TB2_MAX_STEPS:-120}"
export TB2_CONCURRENT="${TB2_CONCURRENT:-2}"
export HOLDOUT_CONCURRENT="${HOLDOUT_CONCURRENT:-2}"
# Each TB2 task pulls its own multi-GB image onto the node's root disk (not the
# shared FS), and this chain touches all 89. Without pruning the store fills
# mid-run and `docker compose up` then fails during the pull, recording a plain
# FAIL that is indistinguishable from a model that solved nothing.
# Keep images, as the known-good loop3 run did. Per-task deletion pays off only
# when Docker's store is on a small root filesystem; here it is on nvme with
# ~28 TB free, so deleting just forces every later round to re-pull the same
# evolve-set images. The periodic prune and the free-space floor below still
# reclaim if that assumption ever stops holding.
export TB2_DELETE_IMAGES="${TB2_DELETE_IMAGES:-0}"
export TB2_PRUNE_EVERY="${TB2_PRUNE_EVERY:-10}"
export TB2_MIN_FREE_GB="${TB2_MIN_FREE_GB:-30}"

mkdir -p /fsx/home/jixuan.chen/logs "$REPO/outputs/tb21_coevolve"

echo "===== plan ====="
echo "  MODEL_SIZE         : $MODEL_SIZE"
echo "  TB2_DATASET        : $TB2_DATASET"
echo "  REPLICATE          : $REPLICATE"
echo "  N_ITERS            : $N_ITERS"
echo "  EVOLVE_ROUNDS      : $EVOLVE_ROUNDS  (+$EXTRA_EVOLVE_ROUNDS on retry/top-up)"
echo "  split              : 89 -> $((89-N_HOLDOUT)) pool / $N_HOLDOUT holdout  (seed $SPLIT_SEED)"
echo "  EVOLVE_N_TASKS     : $EVOLVE_N_TASKS per iteration, drawn from the pool"
echo "  traj floor         : $MIN_TRAJS  (top up <=$MAX_TOPUP_ROUNDS x +$EXTRA_EVOLVE_ROUNDS rounds)"
echo "  traj cap           : $( ((MAX_TRAJS>0)) && echo "$MAX_TRAJS" || echo 'none' )   per_task=$PER_TASK"
echo "  HARNESS_RATCHET    : $HARNESS_RATCHET"
echo "  RATCHET_ALLOW_TIE  : $RATCHET_ALLOW_TIE  (SYSERR_TOL=$SYSERR_TOL TIE_MAX_SYSERR=$TIE_MAX_SYSERR)"
echo "  META_MODEL         : $META_MODEL"

# Check what the eval subprocess will see, not what this shell sees: the eval
# path runs `python` from a bash script several levels down, and when that name
# resolved to a python without harbor the whole round still "completed" -- 89
# tasks failed in 4 seconds and scored as model failures. Verify by resolution,
# not by assuming PATH was exported correctly above.
_eval_py="$(command -v python || true)"
[[ -n "$_eval_py" ]] || { echo "ERROR: preflight: no 'python' on PATH for the eval subprocess" >&2; exit 2; }
"$_eval_py" -c 'import harbor, terminal_bench' 2>/dev/null || {
  echo "ERROR: preflight: 'python' resolves to $_eval_py, which cannot import harbor/terminal_bench" >&2
  exit 2
}
echo "  eval interpreter   : $_eval_py (harbor OK)"

# Preflight the things the loop cannot recover from, before taking the slot.
"$PYTHON_BIN" - <<'PY' || { echo "ERROR: preflight failed" >&2; exit 2; }
import json, os, sys
from pathlib import Path

repo = Path("/fsx/home/jixuan.chen/harnessx-backup")
bad, warn = [], []

tasks_json = repo / "recipe/tb2_evolver/tasks_all_tb2.json"
if not tasks_json.is_file():
    bad.append(f"missing task list: {tasks_json}")
else:
    tasks = json.loads(tasks_json.read_text())
    print(f"  tasks     : {len(tasks)} ids from {tasks_json.name}")
    if len(tasks) != 89:
        warn.append(f"expected 89 task ids, found {len(tasks)}")

for rel in ("scripts/tb2/run_loop_tb21_coevolve.sh",
            "scripts/tb2/split_tb21_tasks.py",
            "scripts/tb2/build_iter_evolve_tasks_tb21.py",
            "recipe/tb2_sft/src/build_tb2_evolve_sft.py",
            "scripts/evaluate.sh",
            "scripts/evolve.sh",
            "configs/baseline_harness.yaml"):
    if not (repo / rel).exists():
        bad.append(f"missing {rel}")

n_hold = int(os.environ.get("N_HOLDOUT", 40))
n_iter_tasks = int(os.environ.get("EVOLVE_N_TASKS", 18))
pool = 89 - n_hold
if not 0 < n_hold < 89:
    bad.append(f"N_HOLDOUT={n_hold} out of range")
if n_iter_tasks > pool:
    bad.append(f"EVOLVE_N_TASKS={n_iter_tasks} exceeds the {pool}-task pool")
else:
    # Project how rotation drains the pool: each iteration keeps last round's
    # unsolved tasks and refills from the unused remainder. Once the remainder
    # is gone the set shrinks to whatever is still unsolved. Run at the 89%
    # solve rate loop3 measured, which is the worst case for pool lifetime.
    def project_min_set(pool_n, n, iters, solve_rate=0.89):
        unused, carried, smallest = pool_n, 0, n
        for _ in range(iters):
            fresh = min(max(0, n - carried), unused)
            size = carried + fresh
            unused -= fresh
            carried = round(size * (1 - solve_rate))
            smallest = min(smallest, size)
        return smallest

    projected = project_min_set(pool, n_iter_tasks, int(os.environ.get("N_ITERS", 3)))
    print(f"  rotation  : pool {pool}, {n_iter_tasks}/iter -> smallest projected set {projected}")
    if projected < max(8, 0.6 * n_iter_tasks):
        warn.append(
            f"rotation projects a {projected}-task set by the last iteration "
            f"(EVOLVE_N_TASKS={n_iter_tasks}, pool={pool}). Lower EVOLVE_N_TASKS or "
            f"raise the pool with a smaller N_HOLDOUT."
        )

# Resolve the dataset against the registry. A name that does not exist there is
# not reported as a config error: harbor raises per task, every task scores as a
# container that never started, and the round only dies once the env-fault guard
# trips six tasks in. Cheap to check, so check it.
ds = os.environ.get("TB2_DATASET", "terminal-bench@2.0")
try:
    import asyncio as _aio
    from harbor.registry.client.harbor.harbor import HarborRegistryClient

    _meta = _aio.run(HarborRegistryClient().get_dataset_metadata(ds))
    _reg = {t.path.name for t in _meta.task_ids}
    print(f"  dataset   : {ds} resolved, {len(_reg)} task(s)")
    _absent = sorted(set(tasks) - _reg)
    if _absent:
        bad.append(
            f"{len(_absent)} of the {len(tasks)} ids are not in {ds} "
            f"({', '.join(_absent[:5])}...). They would each score as a system error."
        )
except Exception as exc:  # noqa: BLE001
    bad.append(
        f"dataset {ds} does not resolve ({type(exc).__name__}: {exc}). "
        f"Check TB2_DATASET; `harbor` only publishes the versions in its registry."
    )

cache = Path.home() / ".cache" / "harbor" / "tasks"
if cache.is_dir():
    cached = {p.parent.name for p in cache.rglob("*/task.toml")}
    if cached and tasks_json.is_file():
        missing = sorted(set(tasks) - cached)
        print(f"  harbor    : {len(cached)} task(s) cached under {cache}")
        if missing:
            warn.append(
                f"{len(missing)} of the 89 ids are not in the harbor cache "
                f"({', '.join(missing[:5])}...). They may still resolve online for "
                f"{ds}, but verify before trusting the scores."
            )
else:
    warn.append(f"no harbor task cache at {cache}; every image will be pulled cold")

try:
    from huggingface_hub import snapshot_download
    path = snapshot_download("Qwen/Qwen3.5-9B")
    shards = list(Path(path).glob("*.safetensors"))
    print(f"  model     : {path} ({len(shards)} shard(s))")
    if not shards:
        bad.append("Qwen3.5-9B snapshot has no safetensors shards")
except Exception as exc:  # noqa: BLE001
    bad.append(f"could not resolve Qwen/Qwen3.5-9B: {type(exc).__name__}: {exc}")

for w in warn:
    print(f"  WARN: {w}", file=sys.stderr)
if bad:
    print("\n".join("  - " + b for b in bad), file=sys.stderr)
    sys.exit(1)
print("preflight OK")
PY

# The meta-agent's credential depends on which provider META_MODEL names, and
# the evolve stage is the first thing to need it -- which on this chain sat 99
# minutes of eval downstream of the submit. Assert the provider's variable is
# present now, mirroring the branch in scripts/tb2/evolve.sh.
case "$META_MODEL" in
  bedrock/*)
    # Soft: the meta-agent needs it, but a transient API failure here should not
    # kill a job that can retry.
    "$PYTHON_BIN" - <<'PY' || echo "WARN: bedrock preflight failed"
import boto3, os
rt = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION_NAME", "us-west-2"))
print("bedrock-runtime OK", rt.meta.region_name)
PY
    ;;
  anthropic/*)
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] || {
      echo "ERROR: META_MODEL=$META_MODEL needs ANTHROPIC_API_KEY" >&2; exit 2; }
    echo "  meta creds : ANTHROPIC_API_KEY present"
    ;;
  *)
    [[ -n "${OPENAI_API_KEY:-}${SFG_API_KEY:-}" ]] || {
      echo "ERROR: META_MODEL=$META_MODEL needs OPENAI_API_KEY or SFG_API_KEY" >&2; exit 2; }
    echo "  meta creds : OPENAI_API_KEY/SFG_API_KEY present"
    ;;
esac

exec bash "$REPO/scripts/tb2/run_loop_tb21_coevolve.sh"
