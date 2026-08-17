# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
from pathlib import Path

from ..base import tool

_GLOB_TIMEOUT_S = 60.0
_GLOB_MAX_MATCHES = 200

_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')"},
        "path": {
            "type": "string",
            "description": "The directory to search in (default: current directory)",
        },
    },
    "required": ["pattern"],
}


def _resolve_glob_base(path: str) -> Path:
    """Resolve Glob search root without anchoring relative paths at ``/``.

    Meta-agent sandboxes historically used ``workspace_path='/'`` so absolute
    trajectory paths work. Anchoring relative ``**`` globs there walks the
    entire shared filesystem (multi-hour hangs on FSx).
    """
    from ...sandbox.base import get_current_sandbox

    raw = Path(path)
    if raw.is_absolute():
        return raw.expanduser().resolve()

    sandbox = get_current_sandbox()
    if sandbox is None:
        return (Path.cwd() / raw).resolve()

    ws = Path(sandbox.workspace_path).expanduser().resolve()
    if ws == Path("/"):
        return (Path.cwd() / raw).resolve()
    return (ws / raw).resolve()


def _collect_matches(base: Path, pattern: str) -> list[Path]:
    matches = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches


@tool(
    name="Glob",
    description="Find files matching a glob pattern. Returns matching file paths sorted by modification time.",
    input_schema=_SCHEMA,
)
async def glob_tool(pattern: str, path: str = ".") -> str:
    # Resolve base: relative paths are anchored to sandbox workspace_path when
    # active, except when that workspace is filesystem root (see _resolve_glob_base).
    try:
        base = _resolve_glob_base(path)
    except Exception as e:
        return f"Error: {e}"

    if base == Path("/") or str(base) == "/":
        return (
            "Error: refusing to Glob from filesystem root '/'. "
            "Pass an explicit `path` under the repo or output directory "
            "(e.g. path='harnessx' or an absolute project path)."
        )

    loop = asyncio.get_running_loop()
    try:
        matches = await asyncio.wait_for(
            loop.run_in_executor(None, _collect_matches, base, pattern),
            timeout=_GLOB_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return (
            f"Error: Glob timed out after {_GLOB_TIMEOUT_S:.0f}s under `{base}` "
            f"for pattern `{pattern}`. Narrow `path` or the pattern "
            "(avoid unanchored `**/...` over huge trees)."
        )
    except Exception as e:
        return f"Error: {e}"

    if not matches:
        return "No files found matching pattern."

    truncated = len(matches) > _GLOB_MAX_MATCHES
    shown = matches[:_GLOB_MAX_MATCHES]
    body = "\n".join(str(m) for m in shown)
    if truncated:
        body += (
            f"\n\n[... truncated: showing {_GLOB_MAX_MATCHES} of {len(matches)} matches. "
            "Narrow `path` or `pattern`. ...]"
        )
    return body
