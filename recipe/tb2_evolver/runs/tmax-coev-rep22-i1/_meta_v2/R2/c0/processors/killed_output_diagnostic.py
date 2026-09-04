# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""KilledOutputDiagnosticProcessor — recover from opaque SIGKILL/timeout results.

Motivation
----------
The TB2 sandbox wraps every Bash command as
``setsid bash -c <cmd> & _hx_pid=$!; ...; wait $_hx_pid`` with a per-command
timeout (default 30s). When a command hangs — most commonly because the agent
starts a *blocking* long-lived service in the foreground, or backgrounds it
with a bare ``&`` while the wrapping ``wait`` still blocks on a child that
never exits — the whole wrapped invocation is SIGKILLed at the timeout. The
sandbox then returns the single opaque line::

    (exit 137, no output captured)

137 = 128 + SIGKILL(9); 143 = 128 + SIGTERM(15); 124 = GNU ``timeout`` code.
To the model this is indistinguishable from "command produced no output", so
it draws the wrong conclusion ("the service just isn't listening yet"), reruns
the *identical* command, gets the identical opaque line, and loops until the
step/token budget is exhausted (``exit_reason=budget_exceeded``).

This is a harness deficiency, not a model-knowledge gap: the sandbox strips
the one fact the agent needs (the command was *killed*, and why nothing was
captured). The fix surfaces that fact and, on recurrence, injects a *general*
strategy for running persistent background services under a command-timeout
sandbox — start detached with ``nohup ... >log 2>&1 &``, return immediately,
then poll the log/port in a *separate* short command. No task-specific ports,
paths, service names, or code are embedded — the guidance applies to any
long-running-service / daemon / port-forward task in the benchmark.

Scope / safety
--------------
* Fires only when a tool result matches the killed-output shape. Ordinary
  clean or non-empty results pass through untouched (zero cost on the common
  path).
* Injects at most a bounded number of nudges per task and only escalates when
  the *same* opaque result recurs, so a one-off timeout on an otherwise
  progressing task costs a single short message.
* Purely additive: never mutates the killed result itself or drops messages;
  only appends / rewrites a trailing user nudge, respecting the before-model
  contract (replace an existing trailing user message rather than +2 insert).
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Exit codes that mean "the process was killed", not "the command failed
# cleanly with a diagnostic". 137=SIGKILL, 143=SIGTERM, 124=timeout(1), 152.
_KILL_EXIT_TOKENS = ("exit 137", "exit 143", "exit 124", "exit 152")
_NO_OUTPUT_MARKER = "no output captured"

_NUDGE_FIRST = (
    "Your last Bash command was KILLED by the sandbox, not run to completion: "
    "the result `(exit 137, no output captured)` means the wrapped command hit "
    "the per-command time limit and was terminated with SIGKILL before any "
    "output was flushed (exit 137 = 128 + signal 9; 143 = SIGTERM; 124 = "
    "timeout). This almost always happens when a command starts a long-running / "
    "blocking process (a server, proxy, port-forward, `serve_forever`, or a bare "
    "`cmd &` whose parent still waits on a child that never exits) inside the "
    "same command that also tries to test it. Re-running the identical command "
    "will produce the identical kill. Change approach: (1) launch any persistent "
    "service fully detached in its OWN short command, e.g. "
    "`nohup <cmd> >/tmp/svc.log 2>&1 & disown`, and let that command return "
    "immediately; (2) in a SEPARATE later command, inspect `/tmp/svc.log` and "
    "check the port/state — never block on the service in the launch command."
)

_NUDGE_REPEAT = (
    "STOP repeating the killed command. You have now received "
    "`(exit ..., no output captured)` multiple times — the sandbox is SIGKILLing "
    "this command every time because it blocks past the time limit; the output "
    "you want will NEVER appear this way. Do not run the same command again. "
    "Instead, in your next turn issue ONE short command that: writes any "
    "long-running service to a startup wrapper which redirects its output to a "
    "log file and backgrounds it detached (`nohup ... >/tmp/log 2>&1 & disown`), "
    "then returns instantly. Only in a LATER, separate command should you read "
    "the log and probe the port. If you cannot make the service start "
    "non-blocking, capture its startup error by running it foreground with a hard "
    "cap, e.g. `timeout 5 <cmd> 2>&1 | head -50`, so you finally see WHY it dies."
)


def _is_killed_output(result: str) -> bool:
    if not result:
        return False
    low = result.lower()
    if _NO_OUTPUT_MARKER not in low:
        return False
    return any(tok in low for tok in _KILL_EXIT_TOKENS)


class KilledOutputDiagnosticProcessor(MultiHookProcessor):
    """Surface opaque SIGKILL/timeout tool results and break the retry loop.

    ``on_after_tool`` classifies each Bash result; ``on_before_model`` injects a
    one-shot (escalating) actionable diagnostic when the killed-output shape was
    just seen. The killed result itself is passed through unchanged so the model
    still sees the raw sandbox line — the nudge annotates it, never hides it.
    """

    _singleton_group = "tb2_killed_output_diagnostic"
    _order = 6

    def __init__(self, max_nudges: int = 4) -> None:
        self.max_nudges = max(1, int(max_nudges))
        self._consecutive_kills: int = 0
        self._nudges_emitted: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._consecutive_kills = 0
        self._nudges_emitted = 0
        self._pending_nudge = ""
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        result = event.result or ""
        if _is_killed_output(result):
            self._consecutive_kills += 1
            if self._nudges_emitted < self.max_nudges:
                self._pending_nudge = (
                    _NUDGE_REPEAT if self._consecutive_kills >= 2 else _NUDGE_FIRST
                )
        else:
            # A non-killed result means the agent got real signal — reset the
            # streak so a later isolated timeout starts fresh at the gentle nudge.
            self._consecutive_kills = 0
            self._pending_nudge = ""
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        self._nudges_emitted += 1
        msgs = list(event.messages)
        # Contract-safe: if the run loop already appended a trailing user message
        # (e.g. a post-tool continuation), rewrite it rather than +2 insert.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            existing = msgs[-1].content if isinstance(msgs[-1].content, str) else ""
            merged = f"{existing}\n\n{nudge}" if existing else nudge
            msgs[-1] = Message(role="user", content=merged)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._consecutive_kills = 0
        self._nudges_emitted = 0
        self._pending_nudge = ""
        yield event
