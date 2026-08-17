"""Docker build / run / exec helpers for one Tmax taxonomy task."""
from __future__ import annotations

import hashlib
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
    materialize_build_context(container_def, ctx)
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
