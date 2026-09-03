#!/usr/bin/env python3
# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Fan a TB2 eval round out across N model-serving endpoints (one vLLM
replica per GPU) using a shared dynamic claim queue instead of a static
task split.

Why dynamic instead of static: TB2 per-task duration varies a lot (15-30 min
typical; a bad harness config can make a task ~10x slower), so splitting 15
tasks into N even shards up front risks one shard drawing all the slow tasks
while the others finish and sit idle. Each endpoint instead runs a worker
pool that repeatedly claims one task from `task_pool` and only moves on once
it is done, so slow tasks just delay their own worker, not the whole round.

Each claimed task becomes its own single-task `harbor run` invocation (via
the same eval script the non-sharded path already uses, e.g.
eval_local_docker.sh) pointed at that worker's endpoint. Results are merged
afterwards into one canonical job directory shaped exactly like a plain
single-endpoint `harbor run` would have produced (trial subdirs + a
top-level aggregate result.json), so every downstream consumer — score
reading, trajectory .md generation, SFT data building — needs no changes.

Usage:
  python -m recipe.tb2_evolver.scripts.sharded_tb2_eval \\
    --eval-script benchmarks/terminal_bench_2/scripts/eval_local_docker.sh \\
    --tasks-json recipe/tb2_evolver/tasks_sample16_seed42_act15.json \\
    --endpoints-file outputs/runs/<tag>/endpoints.json \\
    --job-name my-round --concurrent-per-endpoint 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_RECIPE_DIR = _SCRIPT_DIR.parent
_PROJECT_ROOT = _RECIPE_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from recipe.tb2_evolver import task_pool  # noqa: E402


def _sanitize(name: str) -> str:
    return name.replace("/", "_")


# ── Docker disk guard ────────────────────────────────────────────────────────
# Every TB2 task pulls its own multi-GB image. Harbor's per-task `--delete`
# handles the common case, but it does not reclaim dangling layers, images left
# by a task that crashed before teardown, or the build cache — and Docker's store
# often sits on a small root filesystem rather than the shared one. When it
# fills, `docker compose up` fails during the pull and the task is recorded as a
# plain FAIL with n_input_tokens=0: infrastructure exhaustion that is
# indistinguishable, in the scores, from a model that solved nothing. Observed
# on the full 89-task set as 45/89 such failures and a reported 2/89.
#
# So: sweep every N completed tasks, and also whenever free space drops below a
# floor. `image prune -a` only removes images no container references, so images
# belonging to in-flight tasks are never touched.
_prune_lock: "asyncio.Lock | None" = None
_docker_root_cache: str | None = None


def _docker_root() -> str:
    global _docker_root_cache
    if _docker_root_cache is None:
        try:
            out = subprocess.run(
                ["docker", "info", "--format", "{{.DockerRootDir}}"],
                capture_output=True, text=True, timeout=30,
            )
            _docker_root_cache = (out.stdout or "").strip() or "/var/lib/docker"
        except Exception:
            _docker_root_cache = "/var/lib/docker"
    return _docker_root_cache


def _containerd_root() -> str:
    """containerd's content store — where image layers are actually written.

    This is NOT `docker info --format {{.DockerRootDir}}`. On a containerd-backed
    Docker, a pull streams layers into
    `<containerd-root>/io.containerd.content.v1.content/ingest/`, and that root is
    configured independently. A host can perfectly well have DockerRootDir on a
    multi-TB NVMe while containerd still sits on a nearly-full 97 GB root
    filesystem — which is exactly what produced
    `failed to copy: ... /var/lib/containerd/...: no space left on device`
    on 63 of 89 tasks while a DockerRootDir-only check reported terabytes free.
    """
    for path in ("/etc/containerd/config.toml",):
        try:
            for line in Path(path).read_text().splitlines():
                st = line.strip()
                if st.startswith("root") and "=" in st:
                    v = st.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
        except Exception:
            continue
    return "/var/lib/containerd"


def _disk_paths() -> list[str]:
    """Every filesystem an image pull can exhaust, de-duplicated by device."""
    out, seen = [], set()
    for p in (_containerd_root(), _docker_root()):
        try:
            dev = os.stat(p).st_dev
        except Exception:
            continue
        if dev not in seen:
            seen.add(dev)
            out.append(p)
    return out or [_docker_root()]


def _free_gb(path: str) -> float | None:
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / 1e9
    except Exception:
        return None


async def _prune_docker(reason: str) -> None:
    paths = _disk_paths()
    before = {p: _free_gb(p) for p in paths}
    cmds = [
        ["docker", "container", "prune", "-f"],   # exited containers only
        ["docker", "image", "prune", "-af"],      # images no container references
        ["docker", "builder", "prune", "-af"],    # build cache
        # Aborted pulls leave orphaned blobs in containerd's ingest directory that
        # no Docker-level prune reaches — and a run that already hit ENOSPC is
        # guaranteed to have left some.
        ["ctr", "-n", "moby", "content", "prune", "references"],
    ]
    for cmd in cmds:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=600)
        except Exception as exc:  # noqa: BLE001 — never let cleanup kill the round
            print(f"[sharded_tb2_eval] prune step {cmd[1]} failed: {exc}", file=sys.stderr)
    parts = []
    for p in paths:
        a, b = before.get(p), _free_gb(p)
        if a is not None and b is not None:
            parts.append(f"{p} {a:.1f}G->{b:.1f}G")
        else:
            parts.append(p)
    print(f"[sharded_tb2_eval] docker prune ({reason}): " + " | ".join(parts), flush=True)


class EvalAborted(RuntimeError):
    """The run was stopped because its results could not be trusted."""


# Set once by whichever worker first detects a systemic fault; every other worker
# checks it and retires, so the run stops in seconds instead of grinding through
# the remaining tasks recording failures that say nothing about the model.
_ABORT_REASON: str | None = None
_ENV_FAIL_STREAK = 0
# Six is above any plausible run of genuinely-unsolvable-but-startable tasks and
# well below the 28-task round size, so a real fault is caught inside one round.
_ENV_FAIL_LIMIT = int(os.environ.get("TB2_ENV_FAIL_LIMIT", "6"))


def _abort(reason: str) -> None:
    global _ABORT_REASON
    if _ABORT_REASON is None:
        _ABORT_REASON = reason
        print(f"[sharded_tb2_eval] ABORT: {reason}", file=sys.stderr, flush=True)


def _env_started(trial_dir: Path | None) -> bool:
    """Did the agent ever run — i.e. did this trial consume a single input token?

    `n_input_tokens == 0` is how a container that never came up presents: the
    verifier still writes a result and it scores as a failure indistinguishable
    from "the model could not solve it". fault_router already classifies these as
    ENV, but by then the round has been scored and the trajectories written.
    """
    if trial_dir is None:
        return False
    rp = Path(trial_dir) / "result.json"
    if not rp.is_file():
        return False
    try:
        obj = json.loads(rp.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool((obj.get("agent_result") or {}).get("n_input_tokens"))


async def _await_disk_space(*, worker_id: str, min_free_gb: float) -> bool:
    """Block this worker until there is room to start another task.

    Returns True when it is safe to claim, False if the wait timed out (in which
    case the run is aborted, because a wait that never resolves means the space
    is held by something outside this run).

    The floor here is deliberately *per task*, not the fleet-wide `min_free_gb`:
    one task needs room for one image. Holding every worker to the fleet-wide
    number would deadlock — all of them would wait for headroom that only exists
    once they stop waiting.
    """
    per_task_gb = max(2.0, float(os.environ.get("TB2_PER_TASK_GB", "3")))
    waited = 0
    limit = int(os.environ.get("TB2_DISK_WAIT_S", "1800"))
    announced = False
    while True:
        free = min((v for v in (_free_gb(p) for p in _disk_paths()) if v is not None),
                   default=None)
        if free is None or free >= per_task_gb:
            if announced:
                print(f"[sharded_tb2_eval] {worker_id} resuming: {free:.1f}G free "
                      f"after waiting {waited}s", flush=True)
            return True
        if not announced:
            print(f"[sharded_tb2_eval] {worker_id} pausing: only {free:.1f}G free, "
                  f"need {per_task_gb:.0f}G to start a task. Waiting for running "
                  f"tasks to release their images (this lowers concurrency, it is "
                  f"not an error).", file=sys.stderr, flush=True)
            announced = True
            await _prune_docker("worker paused on low disk")
        if waited >= limit:
            _abort(
                f"waited {waited}s with only {free:.1f}G free and nothing was released. "
                f"The space is held outside this run — check the host."
            )
            return False
        await asyncio.sleep(20)
        waited += 20


async def _maybe_prune(*, done_count: int, prune_every: int, min_free_gb: float) -> None:
    global _prune_lock
    if _prune_lock is None:
        _prune_lock = asyncio.Lock()
    paths = _disk_paths()
    frees = {p: _free_gb(p) for p in paths}
    tight = min(((v, p) for p, v in frees.items() if v is not None), default=(None, None))
    free, root = tight
    low = free is not None and free < min_free_gb
    periodic = prune_every > 0 and done_count > 0 and done_count % prune_every == 0
    if not (low or periodic):
        return
    if _prune_lock.locked():
        return  # another worker is already sweeping; don't queue a second one
    async with _prune_lock:
        reason = f"free {free:.1f}G < {min_free_gb:.0f}G" if low else f"every {prune_every} tasks"
        await _prune_docker(reason)
        free_after = _free_gb(root) if root else None
        if free_after is not None and free_after < min_free_gb:
            print(
                f"[sharded_tb2_eval] WARNING {root} still only {free_after:.1f}G free after "
                f"pruning. Remaining tasks may fail during image pull and would be scored "
                f"as genuine failures — check this before trusting the result.",
                file=sys.stderr,
                flush=True,
            )
            # No abort here. An earlier version stopped the run below half the soft
            # threshold, but that misreads the situation: with every image ACTIVE
            # the reclaimable total is 0 by definition, and the space returns as
            # tasks finish. `_await_disk_space` throttles claims instead, and only
            # a wait that never resolves — space held by something outside this run
            # — escalates to an abort. Keeping the warning: it is still the earliest
            # signal that the host is tight.


def _task_passed(result_json: Path) -> bool:
    try:
        obj = json.loads(result_json.read_text(encoding="utf-8"))
        reward = (obj.get("verifier_result") or {}).get("rewards", {}).get("reward")
        return isinstance(reward, (int, float)) and float(reward) > 0
    except Exception:
        return False


async def _run_one_task(
    *,
    task: str,
    endpoint: str,
    eval_script: Path,
    job_name_prefix: str,
    jobs_dir: Path,
    max_steps: int | None,
    base_env: dict[str, str],
    log_dir: Path,
    delete_images: bool = False,
) -> tuple[bool, Path | None]:
    """Run a single TB2 task against *endpoint*. Return (passed, trial_dir)."""
    sub_job_name = f"{job_name_prefix}__task-{_sanitize(task)}"
    sub_job_dir = jobs_dir / sub_job_name

    # Snapshot pre-existing trial dirs. Harbor reuses a job dir and *appends* a
    # new randomly-suffixed trial rather than replacing, so after a re-run the
    # dir holds both attempts. Knowing which ones predate this invocation is
    # the only reliable way to attribute the result to *this* run — picking by
    # name sorted the random suffix and silently scored the older attempt.
    pre_existing: set[str] = set()
    if sub_job_dir.is_dir():
        pre_existing = {p.name for p in sub_job_dir.iterdir() if p.is_dir()}

    cmd = ["bash", str(eval_script), "--job-name", sub_job_name, "-n", "1", "-t", task]
    # Forward the jobs dir so results land where the merge step looks for them;
    # without it the eval script falls back to its own built-in default.
    cmd += ["-o", str(jobs_dir)]
    if max_steps is not None:
        cmd += ["--max-steps", str(max_steps)]
    # Each TB2 task has its OWN multi-GB image. Over a 15-task set they all fit;
    # over the full 89 they do not, and Docker's store lives on the (small) root
    # disk, not on the shared filesystem. When it fills, `docker compose up`
    # fails during the image pull and the task is recorded as a plain FAIL with
    # n_input_tokens=0 — infrastructure exhaustion that reads exactly like a
    # model that solved nothing. Deleting each image after its task keeps the
    # footprint bounded to the live set.
    if delete_images:
        cmd += ["--delete-images"]

    env = dict(base_env)
    env["TB2_API_BASE"] = endpoint

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{_sanitize(task)}.log"
    with open(log_path, "wb") as log_f:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_PROJECT_ROOT),
            env=env,
            stdout=log_f,
            stderr=asyncio.subprocess.STDOUT,
        )
        rc = await proc.wait()

    trial_dir: Path | None = None
    passed = False
    if sub_job_dir.is_dir():
        candidates = [
            p for p in sub_job_dir.iterdir() if p.is_dir() and (p / "result.json").is_file()
        ]
        fresh = [p for p in candidates if p.name not in pre_existing]
        pool = fresh or candidates
        if pool:
            # Newest result.json wins. `fresh` is preferred outright; the mtime
            # tiebreak covers the case where every candidate predates this run.
            trial_dir = max(pool, key=lambda p: (p / "result.json").stat().st_mtime)
            passed = _task_passed(trial_dir / "result.json")
        if len(candidates) > 1:
            print(
                f"[sharded_tb2_eval] NOTE task={task}: {len(candidates)} trial dirs present "
                f"({len(fresh)} from this run); scored {trial_dir.name if trial_dir else 'none'}",
                file=sys.stderr,
            )
    if trial_dir is None:
        print(
            f"[sharded_tb2_eval] WARN task={task} endpoint={endpoint} rc={rc} "
            f"produced no result.json (see {log_path})",
            file=sys.stderr,
        )
    return passed, trial_dir


async def _worker(
    *,
    worker_id: str,
    endpoint: str,
    db_path: Path,
    eval_script: Path,
    job_name_prefix: str,
    jobs_dir: Path,
    max_steps: int | None,
    base_env: dict[str, str],
    log_dir: Path,
    claimed: dict[str, Path | None],
    delete_images: bool = False,
    prune_every: int = 10,
    min_free_gb: float = 30.0,
    progress: dict[str, int] | None = None,
) -> None:
    consecutive_errors = 0
    while True:
        # Throttle instead of dying. The disk pressure here is a CONCURRENCY peak,
        # not accumulation: measured live, 11 running tasks held 11 images totalling
        # 10.9 GB with `RECLAIMABLE 0B` — every image was in use, so no amount of
        # pruning could free anything at that instant. Aborting was the wrong
        # response to that; the space comes back on its own as tasks finish and
        # their images are removed. So a worker that finds the disk tight simply
        # stops claiming and waits, which lowers effective concurrency exactly
        # while it needs to be lower and restores it afterwards. Only a wait that
        # never resolves is a real failure.
        if not await _await_disk_space(worker_id=worker_id, min_free_gb=min_free_gb):
            return
        if _ABORT_REASON is not None:
            return
        task = task_pool.claim_next(db_path, worker_id)
        if task is None:
            return
        print(f"[sharded_tb2_eval] {worker_id} <- {task}", flush=True)
        try:
            passed, trial_dir = await _run_one_task(
                task=task,
                endpoint=endpoint,
                eval_script=eval_script,
                job_name_prefix=job_name_prefix,
                jobs_dir=jobs_dir,
                max_steps=max_steps,
                base_env=base_env,
                log_dir=log_dir,
                delete_images=delete_images,
            )
            consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001
            # Never let one worker's failure escape into asyncio.gather: that
            # would cancel every sibling worker mid-`proc.wait()`, which does
            # NOT kill the underlying `harbor run` — the orphans keep running,
            # holding GPUs and writing extra trial dirs into the job dir, and
            # the merge step never runs so the whole round's work is lost.
            # Absorb it, count the task as failed, and let the round finish.
            consecutive_errors += 1
            passed, trial_dir = False, None
            print(
                f"[sharded_tb2_eval] ERROR {worker_id} task={task}: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        # Record the outcome in the in-memory map *before* touching the DB, so
        # a mark_done failure cannot drop a task that actually completed.
        claimed[task] = trial_dir
        try:
            task_pool.mark_done(db_path, task, passed)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[sharded_tb2_eval] ERROR {worker_id} mark_done({task}): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        print(f"[sharded_tb2_eval] {worker_id} -> {task}: {'PASS' if passed else 'FAIL'}", flush=True)

        # Systemic environment failure, whatever the cause (disk, docker daemon,
        # image registry). Counted globally rather than per worker: with 8
        # endpoints a per-worker streak of 6 means 48 wasted tasks, and the whole
        # point is to stop before a round's worth of data is fabricated.
        global _ENV_FAIL_STREAK
        if _env_started(trial_dir):
            _ENV_FAIL_STREAK = 0
        else:
            _ENV_FAIL_STREAK += 1
            if _ENV_FAIL_STREAK >= _ENV_FAIL_LIMIT:
                _abort(
                    f"{_ENV_FAIL_STREAK} consecutive tasks produced no agent tokens — "
                    f"their containers never started. These score as genuine failures "
                    f"but say nothing about the model. Check disk on the image-pull "
                    f"filesystem and the docker daemon."
                )
        if _ABORT_REASON is not None:
            print(f"[sharded_tb2_eval] {worker_id} retiring: {_ABORT_REASON}",
                  file=sys.stderr, flush=True)
            return

        if progress is not None:
            progress["done"] = progress.get("done", 0) + 1
            await _maybe_prune(
                done_count=progress["done"],
                prune_every=prune_every,
                min_free_gb=min_free_gb,
            )
        if consecutive_errors >= 3:
            # Systemic failure on this endpoint (dead replica, fd exhaustion).
            # Stop claiming so remaining tasks go to healthy endpoints instead
            # of being burned through at fail-fast speed.
            print(
                f"[sharded_tb2_eval] {worker_id} retiring after {consecutive_errors} "
                f"consecutive errors on {endpoint}",
                file=sys.stderr,
                flush=True,
            )
            return


async def run_sharded(
    *,
    task_names: list[str],
    endpoints: list[str],
    eval_script: Path,
    job_name: str,
    jobs_dir: Path,
    concurrent_per_endpoint: int,
    max_steps: int | None,
    delete_images: bool = False,
    prune_every: int = 10,
    min_free_gb: float = 30.0,
) -> Path:
    """Run *task_names* across *endpoints* and return the merged job dir."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    pool_dir = jobs_dir / "_pools"
    db_path = pool_dir / f"{job_name}.sqlite3"
    task_pool.init_pool(db_path, task_names)

    base_env = os.environ.copy()
    base_env.setdefault("PYTHONPATH", str(_PROJECT_ROOT))

    log_dir = jobs_dir / "_shard_logs" / job_name
    claimed: dict[str, Path | None] = {}
    progress: dict[str, int] = {"done": 0}

    # Sweep before the first pull so a run never starts on a disk that a previous
    # job already filled.
    for _p in _disk_paths():
        _f = _free_gb(_p)
        print(f"[sharded_tb2_eval] image-pull filesystem {_p}: "
              + (f"{_f:.1f}G free" if _f is not None else "unknown"))
    _tight = min((v for v in (_free_gb(p) for p in _disk_paths()) if v is not None), default=None)
    if _tight is not None and _tight < min_free_gb:
        await _prune_docker("pre-run sweep")
        _tight = min((v for v in (_free_gb(p) for p in _disk_paths()) if v is not None), default=None)
    # Peak transient usage is roughly (concurrent pulls) x (image size), so a
    # small filesystem cannot be rescued by per-task deletion — it needs fewer
    # simultaneous pulls. Say so rather than discovering it 40 minutes in.
    if _tight is not None and _tight < 4.0 * concurrent_per_endpoint * len(endpoints):
        print(
            f"[sharded_tb2_eval] WARNING tightest image-pull filesystem has {_tight:.1f}G free "
            f"but up to {concurrent_per_endpoint * len(endpoints)} images may be pulled at once "
            f"(~4G each). Expect ENOSPC failures scored as task failures. Lower "
            f"TB2_CONCURRENT, or move containerd's root to a larger disk.",
            file=sys.stderr, flush=True,
        )

    # Interleave workers slot-major (ep0-w0, ep1-w0, ... ep7-w0, ep0-w1, ...)
    # rather than endpoint-major (ep0-w0..w4, ep1-w0..w4, ...).
    #
    # This ordering is load-bearing, not cosmetic. `claim_next` is a synchronous
    # sqlite call that runs before a worker's first `await`, so coroutines claim
    # tasks in exactly the order `asyncio.gather` starts them. Under
    # endpoint-major ordering with 15 tasks / 8 endpoints / 5 slots, endpoints
    # 0-2 claimed all 15 tasks before endpoint 3 ever ran — five GPUs sat idle
    # while three each served five concurrent streams. Slot-major ordering gives
    # every endpoint one task before any endpoint gets a second.
    workers = []
    slots = max(1, concurrent_per_endpoint)
    for ci in range(slots):
        for ei, endpoint in enumerate(endpoints):
            workers.append(
                _worker(
                    worker_id=f"ep{ei}-w{ci}",
                    endpoint=endpoint,
                    db_path=db_path,
                    eval_script=eval_script,
                    job_name_prefix=job_name,
                    jobs_dir=jobs_dir,
                    max_steps=max_steps,
                    base_env=base_env,
                    log_dir=log_dir,
                    claimed=claimed,
                    delete_images=delete_images,
                    prune_every=prune_every,
                    min_free_gb=min_free_gb,
                    progress=progress,
                )
            )

    started = time.time()
    await asyncio.gather(*workers)
    elapsed = time.time() - started
    print(
        f"[sharded_tb2_eval] {len(task_names)} task(s) across {len(endpoints)} "
        f"endpoint(s) x{concurrent_per_endpoint} done in {elapsed:.1f}s"
    )

    # Raise instead of merging. Returning a job dir here is what let a systemic
    # environment fault flow downstream as a real measurement: the caller sees a
    # normal path, the evolve gate records 0/28 as a regression, and the routed
    # corpus builder is handed 112 trajectories with nothing in them. Callers
    # must be able to tell "the model scored 0" from "nothing ran".
    if _ABORT_REASON is not None:
        # Discard the claim pool. Tasks burned by the fault are recorded 'done'
        # with passed=0, and init_pool only inserts missing rows — so a re-run
        # after the fix would skip exactly the tasks that never ran and fold
        # them in as model failures, which is the silent corruption this abort
        # exists to prevent.
        try:
            db_path.unlink(missing_ok=True)
        except OSError as exc:
            print(
                f"[sharded_tb2_eval] WARN could not remove claim pool {db_path}: "
                f"{exc}. Delete it before re-running or the tasks it marks done "
                f"will be skipped.",
                file=sys.stderr, flush=True,
            )
        raise EvalAborted(
            f"{_ABORT_REASON}\n"
            f"No results were merged for job '{job_name}'. Fix the cause and re-run."
        )

    # ── Merge into one canonical job dir, same shape a plain single-endpoint
    #    `harbor run` would have produced ──────────────────────────────────
    merged_dir = jobs_dir / job_name
    # Rebuild from scratch. Previously only each task's own destination was
    # cleared, so a retry of the same round left behind trial dirs from the
    # earlier attempt under different random suffixes — producing 16 trial
    # dirs for 15 tasks, after which `read_per_task_results` picked the winner
    # by filesystem iteration order (i.e. non-deterministically between the
    # passing and the crashed attempt) and `_write_task_trajectory_mds` could
    # hand the meta-agent the crashed trajectory for a task scored as passing.
    if merged_dir.exists():
        shutil.rmtree(merged_dir)
    merged_dir.mkdir(parents=True, exist_ok=True)
    n_passed = 0
    n_produced = 0
    missing: list[str] = []
    for task in task_names:
        trial_dir = claimed.get(task)
        if trial_dir is None or not trial_dir.is_dir():
            missing.append(task)
            continue
        n_produced += 1
        dest = merged_dir / trial_dir.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(trial_dir, dest)
        if _task_passed(dest / "result.json"):
            n_passed += 1

    # A task that produced no result.json did not pass, so it belongs in the
    # denominator. Dropping it instead (n_passed / n_produced) would *inflate*
    # the pass rate exactly when infrastructure is breaking — 3 passes out of
    # 10 survivors would report 0.30 for a 15-task round whose true rate is
    # 0.20 — and that number feeds the evolve loop's regression gate, so an
    # infra-induced inflation could promote a harness config that is actually
    # worse. Score against the full requested task set and surface the gap.
    n_total = len(task_names)
    pass_rate = (n_passed / n_total) if n_total else 0.0
    if missing:
        print(
            f"[sharded_tb2_eval] WARN {len(missing)}/{n_total} task(s) produced no result "
            f"and are counted as failures: {', '.join(missing)}",
            file=sys.stderr,
        )
    (merged_dir / "result.json").write_text(
        json.dumps(
            {
                "stats": {"metrics": [{"name": "pass_rate", "mean": pass_rate}]},
                "n_tasks": n_total,
                "n_passed": n_passed,
                "n_produced_results": n_produced,
                "missing_tasks": missing,
                "sharded": True,
                "endpoints": endpoints,
                "elapsed_s": round(elapsed, 1),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    suffix = f" ({len(missing)} missing counted as fail)" if missing else ""
    print(f"[sharded_tb2_eval] merged -> {merged_dir} ({n_passed}/{n_total} = {pass_rate:.3f}){suffix}")
    return merged_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-script", required=True, help="Single-endpoint eval script, e.g. eval_local_docker.sh")
    parser.add_argument("--tasks", action="append", dest="task_names", default=[], help="Task name (repeatable)")
    parser.add_argument("--tasks-json", default=None, help="JSON file with a list of task names")
    parser.add_argument(
        "--endpoints-file",
        default=os.environ.get("TB2_ENDPOINTS_FILE"),
        help="JSON file with a list of api_base URLs (default: env TB2_ENDPOINTS_FILE)",
    )
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--jobs-dir", default=str(_PROJECT_ROOT / ".benchmarks/tb2"))
    parser.add_argument(
        "--concurrent-per-endpoint",
        type=int,
        default=1,
        help="Concurrent claims per endpoint (default: 1). Each endpoint is one GPU/vLLM replica; "
        "raise cautiously — real concurrency ceiling is the replica's KV cache, not this number.",
    )
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument(
        "--delete-images",
        action="store_true",
        default=False,
        help="Delete each task's Docker image after it finishes. Required for the full "
             "89-task set: the images do not co-reside on the root disk.",
    )
    parser.add_argument(
        "--prune-every", type=int, default=10,
        help="Sweep unused Docker images/containers/build-cache after every N completed "
             "tasks (0 disables the periodic sweep; the low-space trigger still applies).",
    )
    parser.add_argument(
        "--min-free-gb", type=float, default=30.0,
        help="Sweep immediately whenever Docker's filesystem drops below this many GB free.",
    )
    args = parser.parse_args()

    task_names = list(args.task_names)
    if args.tasks_json:
        task_names.extend(json.loads(Path(args.tasks_json).read_text(encoding="utf-8")))
    task_names = list(dict.fromkeys(task_names))  # de-dup, preserve order
    if not task_names:
        parser.error("no tasks given (use --tasks / --tasks-json)")

    if not args.endpoints_file:
        parser.error("--endpoints-file is required (or set TB2_ENDPOINTS_FILE)")
    endpoints = json.loads(Path(args.endpoints_file).read_text(encoding="utf-8"))
    if not endpoints:
        parser.error(f"--endpoints-file is empty: {args.endpoints_file}")

    eval_script = Path(args.eval_script).resolve()
    if not eval_script.is_file():
        parser.error(f"--eval-script not found: {eval_script}")

    merged_dir = asyncio.run(
        run_sharded(
            task_names=task_names,
            endpoints=endpoints,
            eval_script=eval_script,
            job_name=args.job_name,
            jobs_dir=Path(args.jobs_dir).resolve(),
            concurrent_per_endpoint=args.concurrent_per_endpoint,
            max_steps=args.max_steps,
            delete_images=args.delete_images,
            prune_every=args.prune_every,
            min_free_gb=args.min_free_gb,
        )
    )
    print(str(merged_dir))


if __name__ == "__main__":
    main()
