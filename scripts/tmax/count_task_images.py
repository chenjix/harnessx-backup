"""Count distinct Tmax task images, or size the image-store space check.

  python count_task_images.py tasks.json [tasks.json ...]
      Unique task ids across JSON lists (legacy sbatch formula).

  python count_task_images.py --budget [--no-shared-base] envs.jsonl [...]
      Prints: missing total need_gb

need_gb is sized off images MISSING from this node, not the full 152.
Jobs 2895/2898 died because the sbatch asked for 20G empty as if nothing
was cached — after a previous coevolve the store is full of tmax-eval
images and only 11–19G remains, which is enough to *run*.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def task_ids(path: str) -> set[str]:
    with open(path) as fh:
        d = json.load(fh)
    if isinstance(d, list):
        return {str(x) for x in d}
    return {str(x) for x in (d.get("task_ids") or d.get("tasks") or [])}


def _docker_images() -> set[str]:
    proc = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        text=True,
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def budget(env_paths: list[str], *, shared_base: bool) -> tuple[int, int, int]:
    # Imported lazily so the legacy task-id path does not need recipe/.
    from recipe.tmax_eval import docker_env

    rows: dict[str, dict] = {}
    for p in env_paths:
        path = Path(p)
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            rows[str(r["task_id"])] = r
    want = {docker_env.image_tag(t, r["container_def"]) for t, r in rows.items()}
    have = _docker_images()
    missing = len(want - have)
    total = len(want)
    # ~0.15G per SHARED_BASE-rebased image, ~0.5G standalone, plus headroom
    # for one in-flight layer and a few rotation images later in the loop.
    if shared_base:
        need = max(6, int(missing * 0.15 + 4))
    else:
        need = max(8, int(missing * 0.5 + 6))
    if missing == 0:
        need = 6
    return missing, total, need


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--budget":
        args = args[1:]
        shared = True
        if args and args[0] == "--no-shared-base":
            shared = False
            args = args[1:]
        if not args:
            print("usage: count_task_images.py --budget [--no-shared-base] envs.jsonl ...", file=sys.stderr)
            sys.exit(2)
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        missing, total, need = budget(args, shared_base=shared)
        print(f"{missing} {total} {need}")
        raise SystemExit(0)

    ids: set[str] = set()
    for p in args:
        ids |= task_ids(p)
    print(len(ids))
