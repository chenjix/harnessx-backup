# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LingeringProcessGuard — surface accidental duplicate background processes at exit.

Motivation
----------
On TB2/Tmax system-administration tasks the external verifier frequently
inspects the *final process state* of the container (e.g. asserts that no
lingering copies of a named service remain). A common failure shape:

  * the agent iterates on a start/deploy/test script,
  * re-runs it several times to verify it works,
  * each run spawns a fresh background service (`&` / `nohup`),
  * only the *last* PID gets cleaned up (the script's own shutdown, or a
    single `kill`), leaving earlier instances orphaned,
  * the verifier then finds multiple lingering processes and fails.

The agent has no visibility into the *cumulative* side effects of its own
iterative testing: each Bash call is stateless from its perspective. This
processor closes that visibility gap.

Mechanism
---------
* Passively tracks whether the agent has launched any background process
  during the run (heuristic scan of Bash commands for `&`, `nohup`,
  `setsid`, `disown`).
* When the agent signals exit intent (a model turn with no tool calls),
  and only if it previously launched background work, it queries the live
  sandbox for currently-running processes and groups them by command
  signature.
* If it finds **duplicate instances of the same non-shell command** — the
  fingerprint of accidental re-run orphans — it injects a single
  informational message listing them and asks the agent to confirm whether
  the duplicates are intended before finishing.

Design constraints
------------------
* **Informational, not destructive.** It never kills anything. Many tasks
  legitimately require a service to keep running after exit; a blanket
  "kill your background jobs" nudge would regress those. This guard only
  flags *duplicates* (>=2 instances of the same command line), which are
  almost never intentional, and leaves the decision to the agent.
* **Fires at most once per task**, and only when duplicates actually exist,
  so it adds zero tokens to the common case (no background work, or a
  single clean service instance).
"""

from __future__ import annotations

import dataclasses
import re
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Heuristic: a Bash command that backgrounds a process.
#  - trailing `&` (not `&&`)
#  - nohup / setsid / disown wrappers
_BG_LAUNCH_RE = re.compile(
    r"(?:(?<![&])&(?![&>])\s*(?:$|[#;\n]))|(?:\bnohup\b)|(?:\bsetsid\b)|(?:\bdisown\b)"
)

# Synthetic keepalive tool used to buy one extra turn so the warning can be
# injected on the next before_model. It is never actually executed.
_PROBE_TOOL = "_tb2_lingering_probe"
_PROBE_ACK = "Process-state check initiated. See the message above."

# ps command: one line per process, columns = "<pid> <full command>".
# Excludes kernel threads ([...]) and the ps invocation itself.
_PS_CMD = (
    "ps -eo pid=,args= 2>/dev/null "
    "| grep -v -E '\\s\\[[^]]*\\]$' "
    "| grep -v -E '(ps -eo|grep -v)' "
    "| head -200"
)

# Command tokens that are shells / harness plumbing — duplicates of these are
# expected (login shells, the agent's own Bash calls) and must NOT be flagged.
_IGNORE_LEADERS = (
    "bash",
    "sh",
    "-bash",
    "-sh",
    "dash",
    "zsh",
    "ps",
    "grep",
    "sleep",
    "tail",
    "cat",
    "sshd",
    "init",
    "systemd",
    "tini",
    "docker-init",
    "su",
    "sudo",
)

_WARN_TEMPLATE = (
    "[LingeringProcessGuard] Before you finish: you launched background "
    "process(es) during this task, and the sandbox currently has MULTIPLE "
    "running instances of the same command — this usually means an earlier "
    "run was never stopped and is now orphaned. Verifiers commonly assert "
    "that no lingering/duplicate service processes remain.\n"
    "Duplicated processes right now:\n{listing}\n"
    "Decide deliberately: if exactly one instance should stay running, "
    "terminate the extra PIDs (e.g. `kill <pid>`); if none should remain, "
    "stop them all. If the duplicates are genuinely intended, ignore this "
    "and finish. Re-check with `ps -eo pid,args` after cleaning up."
)


def _launches_background(command: str) -> bool:
    if not command:
        return False
    return bool(_BG_LAUNCH_RE.search(command))


def _signature(args: str) -> str:
    """Collapse a full command line to a stable signature for grouping.

    Keeps the executable basename plus its first argument, which is enough to
    distinguish distinct services while still grouping re-runs of the same
    service together (e.g. two instances of the same compiled binary, or two
    `python server.py` instances)."""
    parts = args.split()
    if not parts:
        return ""
    exe = parts[0].rsplit("/", 1)[-1]
    if len(parts) >= 2 and not parts[1].startswith("-"):
        return f"{exe} {parts[1].rsplit('/', 1)[-1]}"
    return exe


def _leader(args: str) -> str:
    parts = args.split()
    if not parts:
        return ""
    return parts[0].rsplit("/", 1)[-1]


def _find_duplicates(ps_output: str) -> dict[str, list[str]]:
    """Return {signature: [pid, ...]} for command signatures with >=2 PIDs."""
    groups: dict[str, list[str]] = {}
    for line in ps_output.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, args = line.partition(" ")
        pid = pid.strip()
        args = args.strip()
        if not pid.isdigit() or not args:
            continue
        if _leader(args) in _IGNORE_LEADERS:
            continue
        sig = _signature(args)
        if not sig:
            continue
        groups.setdefault(sig, []).append(pid)
    return {sig: pids for sig, pids in groups.items() if len(pids) >= 2}


class LingeringProcessGuard(MultiHookProcessor):
    """Warn once, at exit intent, about accidental duplicate background processes."""

    _singleton_group = "tb2_lingering_process_guard"
    # Run AFTER the self-verify keepalive (order 90): on the first exit turn
    # self-verify fires its checklist; this guard then fires on a subsequent
    # exit turn, so the two do not contend for the same turn.
    _order = 92

    def __init__(self) -> None:
        self._launched_bg = False
        self._fired = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._launched_bg = False
        self._fired = False
        self._pending_message = ""
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _PROBE_TOOL:
            # Intercept our own keepalive probe: never actually execute it.
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_PROBE_ACK
            )
            return
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") if event.tool_input else ""
            if _launches_background(cmd):
                self._launched_bg = True
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
        if not (exit_intent and self._launched_bg and not self._fired):
            yield event
            return

        self._fired = True

        from harnessx.sandbox.base import get_current_sandbox

        sandbox = get_current_sandbox()
        if sandbox is None:
            yield event
            return

        try:
            ps_output = await sandbox.exec(_PS_CMD, timeout=8)
        except Exception:
            yield event
            return

        duplicates = _find_duplicates(ps_output or "")
        if not duplicates:
            yield event
            return

        lines = []
        for sig, pids in sorted(duplicates.items()):
            lines.append(f"  - `{sig}` \u00d7{len(pids)} (PIDs: {', '.join(pids)})")
        listing = "\n".join(lines)
        self._pending_message = _WARN_TEMPLATE.format(listing=listing)

        # Keep the loop alive one more turn by emitting a single harmless
        # keepalive tool call instead of the empty exit turn. The warning is
        # injected on the next before_model; the probe tool is intercepted in
        # on_before_tool and never executes.
        keepalive = ToolCall(
            id=f"lpg-{uuid.uuid4().hex[:8]}",
            name=_PROBE_TOOL,
            input={},
        )
        yield dataclasses.replace(event, tool_calls=(keepalive,))

    async def on_after_tool(self, event: ToolResultEvent):
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._launched_bg = False
        self._fired = False
        self._pending_message = ""
        yield event
