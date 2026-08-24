#!/usr/bin/env bash
# Pre-build every Tmax task's Docker image, in parallel, before the loop starts.
#
#   SHARED_BASE=1 JOBS=12 bash scripts/tmax/prebuild_tmax_images.sh
#   SHARED_BASE=1 DOCKER_BUILDKIT=0 bash scripts/tmax/prebuild_tmax_images.sh   # no cache copy
#   SHARED_BASE=1 SLIM_APT=1 bash scripts/tmax/prebuild_tmax_images.sh          # see below
#
# Why prebuild: every taxonomy task has its OWN container_def, so 152 tasks means
# 152 image builds, each network-bound. Left to the loop they land inside the
# first evolve round and the first holdout eval, inflating both and mixing
# "docker was slow" into the timing table. Building up front also surfaces a
# broken container_def or a full image store immediately.
#
# Disk is the binding constraint, so this script actively manages it:
#   * SHARED_BASE=1   builds the apt-python3-pip-pytest preamble every def repeats
#                     as one image and rebases the task images on it: ~150MB per
#                     task instead of ~400MB. Tags are unchanged (they hash
#                     container_def), so run_eval still finds the cache.
#   * PRUNE_CACHE_EVERY  BuildKit keeps its own copy of every layer it builds, so
#                     the cache roughly doubles the footprint. Pruned every N
#                     successful builds (0 disables).
#   * MIN_FREE_GB     when the image store drops below this, prune and re-check;
#                     still below → stop cleanly. Grinding on gives you dozens of
#                     bogus "build failed" tasks that would score 0 forever.
#   * DOCKER_BUILDKIT=0  legacy builder: no separate cache copy at all. Try it if
#                     pruning is not enough (deprecated, may be unavailable).
#   * SLIM_APT=1      appends `rm -rf /var/lib/apt/lists/*` to the SAME RUN as
#                     %post, saving ~45MB per image. Caveat: the official image
#                     keeps those lists, so an agent that runs `apt-get install`
#                     mid-task would need `apt-get update` first. Off by default.
#
# Uses recipe.tmax_eval.docker_env tags, so nothing is rebuilt later.

set -euo pipefail
_HX_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
while [[ ! -f "$_HX_SCRIPTS/_common.sh" && "$_HX_SCRIPTS" != "/" ]]; do
  _HX_SCRIPTS="$(dirname "$_HX_SCRIPTS")"
done
ROOT="$(cd "$_HX_SCRIPTS/.." && pwd)"
cd "$ROOT"

ENVS_JSONL="${ENVS_JSONL:-$ROOT/recipe/tb2_sft/data/qwen35_9b_tmax_only200/eval_task_set_with_envs.jsonl $ROOT/recipe/tb2_sft/data/tmax_evolve50/eval_task_set_with_envs.jsonl}"
JOBS="${JOBS:-12}"
WORK_ROOT="${WORK_ROOT:-$ROOT/.tmax_eval_work}"
REPORT="${REPORT:-$ROOT/outputs/tmax_image_prebuild.json}"
REBUILD="${REBUILD:-0}"
SHARED_BASE="${SHARED_BASE:-0}"
SLIM_APT="${SLIM_APT:-0}"
PRUNE_CACHE_EVERY="${PRUNE_CACHE_EVERY:-20}"
MIN_FREE_GB="${MIN_FREE_GB:-6}"
PY="${PYTHON_BIN:-${VLLM_VENV:-$HOME/.venv}/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3)"

mkdir -p "$WORK_ROOT" "$(dirname "$REPORT")"

# How many images are actually MISSING? Sizing the space requirement off the full
# 152 refuses to start a run that only has 42 left to build — which is exactly the
# state a previous partial run leaves behind.
inventory="$(ENVS_JSONL="$ENVS_JSONL" PYTHONPATH="$ROOT" "$PY" - <<'PY'
import json, os, subprocess
from pathlib import Path
from recipe.tmax_eval import docker_env

rows = {}
for p in os.environ["ENVS_JSONL"].split():
    path = Path(p)
    if not path.is_file():
        raise SystemExit(f"ERROR: missing envs jsonl: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[str(r["task_id"])] = r
want = {docker_env.image_tag(t, r["container_def"]) for t, r in rows.items()}
# One listing beats 152 `docker image inspect` calls.
have = set(
    subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        text=True, capture_output=True, timeout=120,
    ).stdout.split()
)
print(f"{len(want - have)} {len(want)}")
PY
)" || { echo "ERROR: could not inventory images (is the docker daemon up?)" >&2; exit 2; }
MISSING_N="${inventory%% *}"
TOTAL_N="${inventory##* }"

# ~0.15G per rebased image, ~0.5G standalone, plus headroom for the layer being
# written and the cache that has not been pruned yet.
if [[ "$SHARED_BASE" == "1" ]]; then
  NEED_GB="$("$PY" -c "print(max(5, int($MISSING_N * 0.15 + 4)))")"
else
  NEED_GB="$("$PY" -c "print(max(8, int($MISSING_N * 0.5 + 6)))")"
fi
echo "[prebuild] images: $MISSING_N of $TOTAL_N missing -> need ~${NEED_GB}G free"
export MIN_IMAGE_GB="${MIN_IMAGE_GB:-$NEED_GB}"

if (( MISSING_N == 0 )); then
  echo "[prebuild] nothing to build — all $TOTAL_N images present"
  exit 0
fi

# Storage + a real one-layer build before launching the remaining builds.
bash "$ROOT/scripts/tmax/docker_preflight.sh" \
  || { echo "ERROR: aborting prebuild — fix the docker storage first" >&2; exit 2; }

echo "[prebuild] jobs=$JOBS shared_base=$SHARED_BASE slim_apt=$SLIM_APT" \
     "buildkit=${DOCKER_BUILDKIT:-1} prune_every=$PRUNE_CACHE_EVERY min_free=${MIN_FREE_GB}G"
echo "[prebuild] docker disk before:"; docker system df || true

ENVS_JSONL="$ENVS_JSONL" JOBS="$JOBS" WORK_ROOT="$WORK_ROOT" REPORT="$REPORT" \
REBUILD="$REBUILD" SHARED_BASE="$SHARED_BASE" SLIM_APT="$SLIM_APT" \
PRUNE_CACHE_EVERY="$PRUNE_CACHE_EVERY" MIN_FREE_GB="$MIN_FREE_GB" \
PYTHONPATH="$ROOT" "$PY" - <<'PY'
import json, os, re, shutil, subprocess, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from recipe.tmax_eval import docker_env
from recipe.tmax_eval.singularity_to_dockerfile import singularity_to_dockerfile

work_root = Path(os.environ["WORK_ROOT"])
jobs = int(os.environ["JOBS"])
rebuild = os.environ.get("REBUILD") == "1"
shared_base = os.environ.get("SHARED_BASE") == "1"
slim_apt = os.environ.get("SLIM_APT") == "1"
prune_every = int(os.environ.get("PRUNE_CACHE_EVERY") or 0)
min_free_gb = float(os.environ.get("MIN_FREE_GB") or 0)
report_path = Path(os.environ["REPORT"])

BASE_TAG = "hx-tmax-base:1"
BASE_DOCKERFILE = """\
FROM ubuntu:22.04
# The preamble every taxonomy container_def repeats. Building it once and
# rebasing the task images on it is what keeps 152 images from each carrying
# their own copy of python3 + pip + apt state. No ENV here on purpose: the plain
# build only exports DEBIAN_FRONTEND inside the %post shell, and a lingering ENV
# would be visible to the agent at run time.
RUN DEBIAN_FRONTEND=noninteractive apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip \
 && pip3 install pytest
"""

# Every one of these means "the image store is full", whatever layer noticed it.
_ENOSPC = re.compile(
    r"no space left on device|ResourceExhausted|create prepare snap|"
    r"disk quota exceeded|cannot allocate memory|input/output error",
    re.I,
)


def _docker(args, timeout=1800):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout)


def _store_path() -> Path:
    # Same rule as docker_preflight.sh: the containerd snapshotter is the real
    # image store on these nodes, not `docker info`'s DockerRootDir.
    if Path("/var/lib/containerd").is_dir():
        return Path("/var/lib/containerd")
    out = _docker(["docker", "info", "--format", "{{.DockerRootDir}}"], timeout=60)
    p = Path((out.stdout or "").strip() or "/var/lib/docker")
    return p if p.is_dir() else Path("/")


STORE = _store_path()


def free_gb() -> float:
    try:
        return shutil.disk_usage(STORE).free / 1024**3
    except OSError:
        return -1.0


def prune_cache(what: str = "cache") -> float:
    before = free_gb()
    _docker(["docker", "builder", "prune", "-f"], timeout=900)
    after = free_gb()
    print(f"[prebuild] pruned build {what}: {before:.1f}G -> {after:.1f}G free on {STORE}")
    return after


rows: dict[str, dict] = {}
for p in os.environ["ENVS_JSONL"].split():
    path = Path(p)
    if not path.is_file():
        raise SystemExit(f"ERROR: missing envs jsonl: {path}")
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                r = json.loads(line)
                rows[str(r["task_id"])] = r

tags = {tid: docker_env.image_tag(tid, r["container_def"]) for tid, r in rows.items()}
print(f"[prebuild] {len(rows)} task(s) -> {len(set(tags.values()))} unique image tag(s)")
print(f"[prebuild] image store {STORE}: {free_gb():.1f}G free")


def have_image(tag: str) -> bool:
    return _docker(["docker", "image", "inspect", tag], timeout=60).returncode == 0


def build_shared_base() -> None:
    if have_image(BASE_TAG) and not rebuild:
        print(f"[prebuild] shared base {BASE_TAG} already present")
        return
    ctx = work_root / "build" / "_hx_shared_base"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "Dockerfile").write_text(BASE_DOCKERFILE, encoding="utf-8")
    print(f"[prebuild] building shared base {BASE_TAG} ...")
    t0 = time.time()
    proc = _docker(["docker", "build", "--network=host", "-t", BASE_TAG, str(ctx)])
    if proc.returncode != 0:
        raise SystemExit(
            "ERROR: shared base build failed:\n" + proc.stdout[-1500:] + "\n" + proc.stderr[-3000:]
        )
    print(f"[prebuild] shared base ready in {time.time() - t0:.0f}s")


def build_one(tid: str, r: dict) -> str:
    tag = docker_env.image_tag(tid, r["container_def"])
    if have_image(tag) and not rebuild:
        return tag
    dockerfile, post_script = singularity_to_dockerfile(r["container_def"])
    rebase = shared_base and dockerfile.startswith("FROM ubuntu:22.04\n")
    if rebase:
        dockerfile = dockerfile.replace("FROM ubuntu:22.04\n", f"FROM {BASE_TAG}\n", 1)
    if slim_apt:
        # Same RUN, so the lists never enter the layer at all.
        dockerfile = dockerfile.replace(
            "RUN chmod +x /tmp/tmax_post.sh && bash /tmp/tmax_post.sh",
            "RUN chmod +x /tmp/tmax_post.sh && bash /tmp/tmax_post.sh "
            "&& rm -rf /var/lib/apt/lists/* /root/.cache/pip",
            1,
        )
    ctx = work_root / "build" / tid
    ctx.mkdir(parents=True, exist_ok=True)
    for stale in ctx.iterdir():
        if stale.is_file():
            stale.unlink()
    (ctx / "tmax_post.sh").write_text(post_script, encoding="utf-8")
    (ctx / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    proc = _docker(["docker", "build", "--network=host", "-t", tag, str(ctx)])
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {tid} (rc={proc.returncode}):\n"
            f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
        )
    return tag


def one(item):
    tid, r = item
    t0 = time.time()
    try:
        return tid, build_one(tid, r), round(time.time() - t0, 1), None, False
    except Exception as e:  # noqa: BLE001 — one bad def must not kill the batch
        txt = f"{type(e).__name__}: {e}"
        # Classify on the FULL text, not the truncated tail: the ENOSPC line is
        # often thousands of chars into apt/pip output, which is why a truncated
        # error looked like 27 unrelated failures last time. And when the store is
        # already at the floor, treat ANY failure as out-of-space — a build that
        # dies while apt or go cannot write is not an interesting per-task bug.
        out_of_space = bool(_ENOSPC.search(txt))
        if not out_of_space and min_free_gb and 0 <= free_gb() < min_free_gb * 1.5:
            out_of_space = True
        return tid, None, round(time.time() - t0, 1), txt[:3000], out_of_space


if shared_base:
    build_shared_base()

t_start = time.time()
ok, failed, secs = [], {}, {}
aborted = None
with ThreadPoolExecutor(max_workers=jobs) as ex:
    futs = {ex.submit(one, it): it[0] for it in rows.items()}
    done = 0
    for f in as_completed(futs):
        tid, tag, dt, err, enospc = f.result()
        done += 1
        secs[tid] = dt
        if err:
            failed[tid] = err
            if enospc:
                print(f"[{done}/{len(rows)}] FAIL {tid} ({dt}s) image store FULL")
            elif len(failed) == 1:
                print(f"[{done}/{len(rows)}] FAIL {tid} ({dt}s) — full error:")
                for ln in err.splitlines()[-25:]:
                    print(f"    {ln}")
            else:
                tail = [l for l in err.splitlines() if l.strip()][-1:] or [""]
                print(f"[{done}/{len(rows)}] FAIL {tid} ({dt}s) {tail[0][:160]}")
            if enospc:
                # Prune once and see if that bought room; if not, stop. Continuing
                # only manufactures tasks that score 0 with status=error forever.
                if prune_every and prune_cache("cache after ENOSPC") >= min_free_gb:
                    print("[prebuild] space recovered — continuing "
                          f"({len(ok)} built, {len(failed)} failed so far)")
                else:
                    aborted = "image store full"
            elif len(failed) >= 3 and not ok:
                aborted = "3 failures, 0 successes — environmental"
        else:
            ok.append(tid)
            print(f"[{done}/{len(rows)}] ok   {tid} ({dt}s)")
            if prune_every and len(ok) % prune_every == 0:
                prune_cache()
            # free_gb() returns -1 when the store path is not statable (no
            # permission); an unknown number must not read as "disk full".
            cur = free_gb()
            if min_free_gb and 0 <= cur < min_free_gb:
                if prune_cache("cache (low space)") < min_free_gb:
                    aborted = f"less than {min_free_gb}G free on {STORE}"
        if aborted:
            print(f"\n[prebuild] STOPPING: {aborted}")
            for fut in futs:
                fut.cancel()
            break

wall = round(time.time() - t_start, 1)
vals = sorted(secs.values())
enospc_n = sum(1 for v in failed.values() if _ENOSPC.search(v))
summary = {
    "n_tasks": len(rows),
    "n_unique_images": len(set(tags.values())),
    "n_ok": len(ok),
    "n_failed": len(failed),
    "n_failed_out_of_space": enospc_n,
    "n_failed_other": len(failed) - enospc_n,
    "wall_seconds": wall,
    "jobs": jobs,
    "shared_base": shared_base,
    "slim_apt": slim_apt,
    "store": str(STORE),
    "free_gb_after": round(free_gb(), 1),
    "aborted": aborted,
    "per_image_seconds": {
        "min": vals[0] if vals else 0,
        "median": vals[len(vals) // 2] if vals else 0,
        "p90": vals[int(len(vals) * 0.9)] if vals else 0,
        "max": vals[-1] if vals else 0,
    },
    "failures": failed,
}
report_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, indent=2))
print(f"report: {report_path}")
if failed:
    print(
        f"\n{len(failed)} image(s) missing ({enospc_n} out of space, "
        f"{len(failed) - enospc_n} other). Re-running this script retries ONLY "
        f"those — the {len(ok)} built images are skipped via docker image inspect."
    )
    raise SystemExit(1)
PY

echo "[prebuild] docker disk after:"; docker system df || true
