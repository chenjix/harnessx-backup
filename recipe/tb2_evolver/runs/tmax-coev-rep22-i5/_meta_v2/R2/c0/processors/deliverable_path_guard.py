"""DeliverablePathGuard — ground the exit check against the task's own paths.

Motivation (evolve-set evidence, carries NO task-specific constants)
--------------------------------------------------------------------
The TB2 grader is path-exact: a solution with correct logic but written to the
wrong path — or never placed at the required path — is a hard zero regardless of
content (see tb2-playbook "Correct logic, wrong path"). The existing
``CustomSelfVerifyProcessor`` injects a *generic* prose checklist on exit, but
the agent routinely satisfies it against a path of its own choosing and exits
anyway. Example: task_000010 was told to write ``/home/user/operator.py``; it
built a fully working script, hit a self-imposed constraint (naming an actively
imported script ``operator.py`` shadows the stdlib ``operator`` module *when run
from that cwd*), relocated the deliverable to ``k8s_operator.py``, ``ls``-ed
*that* path to satisfy the generic checklist, and exited. Two of three grader
tests already passed; the only failure was ``/home/user/operator.py`` missing.

This processor closes that class mechanically. It fires purely on a structural
property — an absolute file path that the *task prompt itself* names but that
does NOT exist on disk at the moment the agent tries to finish. Input paths the
task mentions (files to read) already exist, so they never trip; only genuinely
missing deliverables surface. It carries no hardcoded paths, task ids, or
regexes tied to any one task — every path is derived at runtime from that run's
own task description.

Hook shape (mirrors CustomSelfVerifyProcessor):
- on_task_start: extract candidate absolute paths from ``task_description``.
- on_after_model: on an exit-intent turn (finish_reason end_turn/stop, no tool
  calls), ``test -e`` each candidate path in the sandbox. If any are missing,
  block the exit once (inject a keepalive tool call) and stage a message naming
  the SPECIFIC missing required paths.
- on_before_model: flush the staged message as one user turn.
- on_before_tool: swallow the keepalive tool call (no real execution).

Safety: the block fires at most ``max_blocks`` times per task. If the agent
still cannot produce the paths after that, the guard goes silent and lets the
run finish — a genuinely impossible deliverable must not trap the loop.
"""

from __future__ import annotations

import dataclasses
import re
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskStartEvent,
    TaskEndEvent,
    ToolCall,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

_GUARD_TOOL = "_deliverable_path_guard"
_GUARD_ACK = "Deliverable-path check initiated. See the message above."

# Absolute paths rooted under a real workspace root, with a filename that has an
# extension (so we target files, not directories the task may not require).
# Deliberately conservative — a false negative (miss a required path) is inert;
# a false positive that flagged an input path is self-corrected because input
# files already exist on disk and therefore never appear in the "missing" set.
_ROOTS = r"home|app|tmp|opt|etc|var|root|srv|usr|data|workspace|output"
_PATH_RE = re.compile(
    r"(?<![\w/])(/(?:" + _ROOTS + r")/[A-Za-z0-9._\-/]*[A-Za-z0-9_\-]\.[A-Za-z0-9]{1,6})"
)

_MSG_HEAD = (
    "[DeliverablePathGuard] You are about to finish, but the task description "
    "names required output path(s) that do NOT exist on disk right now:\n"
)
_MSG_TAIL = (
    "\nThe grader checks these exact paths. A correct solution written to a "
    "different path (e.g. a renamed file) still scores zero. If you relocated a "
    "working artifact to avoid a runtime issue, put a copy back at the EXACT "
    "required path (`cp` is fine — the file only needs to exist there; you do "
    "not have to be able to import or execute it from that location). Then "
    "re-verify with `ls -l` on each path above before finishing."
)


class DeliverablePathGuard(MultiHookProcessor):
    _singleton_group = "tb2_deliverable_path_guard"
    _order = 91  # just after CustomSelfVerifyProcessor (_order=90)

    def __init__(self, max_blocks: int = 2, max_paths: int = 12) -> None:
        self.max_blocks = int(max_blocks)
        self.max_paths = int(max_paths)
        self._candidates: list[str] = []
        self._blocks_used = 0
        self._pending_message = ""

    def _reset(self) -> None:
        self._candidates = []
        self._blocks_used = 0
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        desc = event.task_description or ""
        seen: list[str] = []
        for m in _PATH_RE.finditer(desc):
            p = m.group(1).rstrip(".,);:'\"`")
            if p not in seen:
                seen.append(p)
            if len(seen) >= self.max_paths:
                break
        self._candidates = seen
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if not exit_intent or not self._candidates or self._blocks_used >= self.max_blocks:
            yield event
            return

        missing = await self._missing_paths()
        if not missing:
            yield event
            return

        self._blocks_used += 1
        listing = "\n".join(f"  - {p}" for p in missing)
        self._pending_message = _MSG_HEAD + listing + _MSG_TAIL
        keepalive = ToolCall(
            id=f"dpg-{uuid.uuid4().hex[:8]}",
            name=_GUARD_TOOL,
            input={},
        )
        yield dataclasses.replace(event, tool_calls=(keepalive,))

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _GUARD_TOOL:
            yield dataclasses.replace(event, approved=False, synthetic_result=_GUARD_ACK)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event

    async def _missing_paths(self) -> list[str]:
        try:
            from harnessx.sandbox.base import get_current_sandbox

            sandbox = get_current_sandbox()
        except Exception:
            return []
        if sandbox is None:
            return []

        missing: list[str] = []
        for path in self._candidates:
            # Reject shell-metachar paths defensively before interpolating.
            if any(c in path for c in "'\"`$;&|<>()\n\\ "):
                continue
            try:
                out = await sandbox.exec(
                    f"test -e '{path}' && echo __DPG_OK__ || echo __DPG_MISS__",
                    timeout=6,
                )
            except Exception:
                continue
            if "__DPG_MISS__" in (out or "") and "__DPG_OK__" not in (out or ""):
                missing.append(path)
        return missing
