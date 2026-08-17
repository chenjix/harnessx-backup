"""Convert Tmax taxonomy Singularity ``container_def`` → Dockerfile.

All 2.2k taxonomy defs use::

    Bootstrap: docker
    From: ubuntu:22.04

    %post
        ...

We materialize the ``%post`` body as ``/tmp/tmax_post.sh`` and ``RUN`` it so
nested quotes / heredocs inside ``python3 -c '...'`` survive intact.
"""
from __future__ import annotations

import re
from pathlib import Path


_FROM_RE = re.compile(r"^From:\s*(\S+)\s*$", re.I | re.M)
_BOOTSTRAP_RE = re.compile(r"^Bootstrap:\s*(\S+)\s*$", re.I | re.M)


def singularity_to_dockerfile(container_def: str) -> tuple[str, str]:
    text = (container_def or "").replace("\r\n", "\n")
    if not text.strip():
        raise ValueError("empty container_def")

    boot = _BOOTSTRAP_RE.search(text)
    if boot and boot.group(1).lower() != "docker":
        raise ValueError(f"unsupported Bootstrap: {boot.group(1)}")

    frm = _FROM_RE.search(text)
    if not frm:
        raise ValueError("container_def missing From: line")
    base = frm.group(1)

    # Everything after the first %post section header until next %section or EOF.
    # DOTALL so the post body can span lines; MULTILINE so ^ matches section headers.
    m = re.search(r"(?ims)^%post\s*\n(.*?)(?=^%\w|\Z)", text)
    if not m:
        raise ValueError("container_def missing %post section")
    post_body = m.group(1)
    # Strip a uniform leading indent common in Singularity defs, but keep
    # relative indentation inside the script.
    lines = post_body.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        indents = [len(l) - len(l.lstrip(" ")) for l in lines if l.strip()]
        common = min(indents) if indents else 0
        if common:
            lines = [l[common:] if len(l) >= common else l for l in lines]
    post_script = "\n".join(lines) + "\n"

    dockerfile = f"""\
FROM {base}
# Generated from Tmax taxonomy Singularity container_def
WORKDIR /home/user
RUN mkdir -p /home/user /tmp && chmod 755 /home/user
COPY tmax_post.sh /tmp/tmax_post.sh
RUN chmod +x /tmp/tmax_post.sh && bash /tmp/tmax_post.sh
# Agent + pytest run as root for simplicity (taxonomy seeds chmod 777 /home/user).
ENV HOME=/home/user
WORKDIR /home/user
"""
    return dockerfile, post_script


def materialize_build_context(container_def: str, out_dir: Path) -> Path:
    """Write Dockerfile + tmax_post.sh into *out_dir*; return Dockerfile path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dockerfile, post_script = singularity_to_dockerfile(container_def)
    (out_dir / "tmax_post.sh").write_text(post_script)
    df = out_dir / "Dockerfile"
    df.write_text(dockerfile)
    return df
