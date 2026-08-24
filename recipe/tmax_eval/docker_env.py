"""Docker build / run / exec helpers for one Tmax taxonomy task."""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .singularity_to_dockerfile import materialize_build_context


def _run(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
    )


def image_tag(task_id: str, container_def: str) -> str:
    h = hashlib.sha1(container_def.encode()).hexdigest()[:12]
    safe = task_id.replace("/", "_")
    return f"tmax-eval:{safe}-{h}"


_SHARED_BASE_OK: bool | None = None


def _shared_base_tag() -> str | None:
    """Tag of an image holding the preamble every taxonomy def repeats, if usable.

    Set ``TMAX_SHARED_BASE_TAG`` (see scripts/tmax/prebuild_tmax_images.sh
    SHARED_BASE=1) to rebase task images onto it. Every taxonomy container_def is
    ``FROM ubuntu:22.04`` plus a %post that reinstalls python3/pip/pytest, and the
    whole %post becomes one layer — so without this each task image carries its
    own ~0.4G copy of the same interpreter. Rebasing keeps the per-task layer down
    to what the task itself adds, which is the difference between ~60G and ~15G
    for a 152-task set. The image TAG is unchanged either way.
    """
    global _SHARED_BASE_OK
    tag = (os.environ.get("TMAX_SHARED_BASE_TAG") or "").strip()
    if not tag:
        return None
    if _SHARED_BASE_OK is None:
        _SHARED_BASE_OK = _run(["docker", "image", "inspect", tag], timeout=30).returncode == 0
    return tag if _SHARED_BASE_OK else None


def _maybe_rebase(dockerfile: Path) -> None:
    """Point a materialized Dockerfile at the shared base, when there is one."""
    tag = _shared_base_tag()
    if tag is None:
        return
    text = dockerfile.read_text(encoding="utf-8")
    # Only ubuntu:22.04 defs — do not silently swap someone else's base out.
    if not text.startswith("FROM ubuntu:22.04\n"):
        return
    dockerfile.write_text(
        text.replace("FROM ubuntu:22.04\n", f"FROM {tag}\n", 1), encoding="utf-8"
    )


def build_image(
    task_id: str,
    container_def: str,
    work_root: Path,
    *,
    rebuild: bool = False,
    build_timeout: float = 1800,
) -> str:
    tag = image_tag(task_id, container_def)
    if not rebuild:
        probe = _run(["docker", "image", "inspect", tag], timeout=30)
        if probe.returncode == 0:
            return tag
    ctx = work_root / "build" / task_id
    ctx.mkdir(parents=True, exist_ok=True)
    for p in ctx.iterdir():
        if p.is_file():
            p.unlink()
    _maybe_rebase(materialize_build_context(container_def, ctx))
    proc = _run(
        ["docker", "build", "--network=host", "-t", tag, str(ctx)],
        timeout=build_timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {task_id} (rc={proc.returncode}):\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
        )
    return tag


def start_container(task_id: str, image: str, *, network: str = "bridge") -> str:
    """Start a long-lived container. Default bridge so apt-using agent cmds can work."""
    name = f"tmax-{task_id.replace('_', '')[:20]}-{int(time.time())}"[:63]
    proc = _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--workdir",
            "/home/user",
            image,
            "sleep",
            "infinity",
        ],
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker run failed for {task_id}: {proc.stderr or proc.stdout}"
        )
    return (proc.stdout or "").strip() or name


def stop_container(container: str, *, remove: bool = True) -> None:
    _run(["docker", "kill", container], timeout=60)
    if remove:
        _run(["docker", "rm", "-f", container], timeout=60)


def exec_in(
    container: str,
    command: str,
    *,
    timeout: float = 120,
    workdir: str = "/home/user",
) -> tuple[int, str]:
    try:
        proc = _run(
            [
                "docker",
                "exec",
                "-w",
                workdir,
                container,
                "bash",
                "-lc",
                command,
            ],
            timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        return 124, f"Error: command timed out after {timeout}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def write_file(container: str, path: str, content: str) -> None:
    # Ensure parent dir exists, then stream bytes via stdin.
    parent = str(Path(path).parent)
    _run(
        ["docker", "exec", container, "bash", "-lc", f"mkdir -p {parent}"],
        timeout=30,
    )
    proc = subprocess.run(
        ["docker", "exec", "-i", container, "bash", "-lc", f"cat > {path}"],
        input=content,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"failed to write {path} in {container}: {proc.stderr or proc.stdout}"
        )


def run_pytest(container: str, test_src: str, *, dest: str) -> dict[str, Any]:
    write_file(container, dest, test_src)
    # Ensure pytest is available (most images install it in %post).
    rc, out = exec_in(
        container,
        f"python3 -m pytest -q {dest} 2>&1 || python3 -m pytest -q {dest} 2>&1",
        timeout=180,
    )
    # If pytest module missing, try installing once.
    if rc != 0 and "No module named pytest" in out:
        exec_in(container, "pip3 install -q pytest", timeout=180)
        rc, out = exec_in(container, f"python3 -m pytest -q {dest} 2>&1", timeout=180)
    return {"passed": rc == 0, "rc": rc, "output": out[-8000:]}
