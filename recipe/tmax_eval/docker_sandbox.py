# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Sandbox adapter: route HarnessX Bash tool calls into a live Tmax Docker container.

Tmax owns container lifecycle (``docker_env.start_container`` / ``stop_container``);
this provider is a no-op acquire/release wrapper, same pattern as HarborSandbox.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harnessx.sandbox.base import Sandbox, SandboxProvider

from . import docker_env

if TYPE_CHECKING:
    from harnessx.workspace.workspace import Workspace

_OUTPUT_LIMIT = 12000
_PGID_FILE = "/tmp/_hx_exec_pgid"


class TmaxDockerSandbox(Sandbox):
    """``Sandbox.exec`` → ``docker_env.exec_in`` on a pre-started container."""

    def __init__(
        self,
        container: str,
        workspace_path: str = "/home/user",
        output_limit: int | None = _OUTPUT_LIMIT,
    ) -> None:
        self._container = container
        self._workspace_path = workspace_path
        self._output_limit = output_limit

    @property
    def workspace_path(self) -> str:
        return self._workspace_path

    def _trim(self, text: str) -> str:
        if self._output_limit and len(text) > self._output_limit:
            removed = len(text) - self._output_limit
            return (
                text[: self._output_limit]
                + f"\n[...output truncated: {removed} chars not shown. "
                "Use head/tail/grep to target specific output.]"
            )
        return text

    async def kill_running(self) -> None:
        try:
            await asyncio.to_thread(
                docker_env.exec_in,
                self._container,
                (
                    f"_pid=$(cat {_PGID_FILE} 2>/dev/null);"
                    f' [ -n "$_pid" ] || exit 0;'
                    f' kill -15 -"$_pid" 2>/dev/null;'
                    f" sleep 1;"
                    f' kill -9 -"$_pid" 2>/dev/null;'
                    f" rm -f {_PGID_FILE}"
                ),
                timeout=10.0,
                workdir=self._workspace_path,
            )
        except Exception:
            pass

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float = 30.0,
    ) -> str:
        workdir = cwd or self._workspace_path
        try:
            rc, out = await asyncio.to_thread(
                docker_env.exec_in,
                self._container,
                command,
                timeout=float(timeout),
                workdir=workdir,
            )
        except Exception:
            try:
                await asyncio.shield(self.kill_running())
            except Exception:
                pass
            raise
        text = out or ""
        if rc != 0 and f"(exit {rc})" not in text:
            text = f"{text}\n(exit {rc})" if text else f"(exit {rc}, no output captured)"
        if not text:
            text = f"(exit {rc}, no output captured)"
        return self._trim(text)


class TmaxDockerSandboxProvider(SandboxProvider):
    """Provider for an already-running Tmax Docker container."""

    def __init__(
        self,
        container: str,
        workspace_path: str = "/home/user",
        output_limit: int | None = _OUTPUT_LIMIT,
    ) -> None:
        self._container = container
        self._workspace_path = workspace_path
        self._output_limit = output_limit

    async def acquire(
        self,
        hint_id: str | None = None,
        workspace: "Workspace | None" = None,
    ) -> TmaxDockerSandbox:
        return TmaxDockerSandbox(
            self._container,
            workspace_path=self._workspace_path,
            output_limit=self._output_limit,
        )

    async def release(self, sandbox: Sandbox) -> None:
        pass

    async def shutdown(self) -> None:
        pass


__all__ = ["TmaxDockerSandbox", "TmaxDockerSandboxProvider"]
