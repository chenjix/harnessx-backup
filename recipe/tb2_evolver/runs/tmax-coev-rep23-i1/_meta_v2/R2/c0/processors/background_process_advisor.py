# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""BackgroundProcessAdvisorProcessor for Tmax (and TB2-style) agents.

Closes a systemic, harness-specific no-progress failure mode that the
existing loop / length recovery processors do NOT catch:

The single ``Bash`` tool runs each command in its own short-lived shell.
A process started with a trailing ``&`` (or ``python3 server.py &``,
``socat ... &``, etc.) is a child of *that* shell; when the tool call
returns the shell exits and the backgrounded child is torn down. The
agent observes this only as a bare exit status — ``(exit 137 ...)``
(SIGKILL) or ``(exit 143 ...)`` (SIGTERM) with *no output captured* — and
as an accumulating pile of ``<defunct>`` (zombie) processes in later
``ps`` listings.

Neither signal is self-explanatory, and small 4B-class models reliably
**misdiagnose** it: they conclude "zombie processes are blocking new
processes" and enter a spiral of ``kill -9`` / ``pkill`` calls against
processes that are *already dead and unkillable* (a zombie is reaped by
its parent, never by ``kill``). Observed across multiple Tmax
trajectories: dozens of consecutive ``exit 137`` results and 25-79
``<defunct>`` mentions, burning the entire step budget without ever
persisting the background service the task required.

This is a genuine harness deficiency, not a domain-knowledge gap: the
teardown-on-tool-return behaviour and the harmlessness of zombies are
facts about the *sandbox/tool layer* that the agent cannot infer from
the task description. This processor supplies that runtime context.

Mechanism (benchmark-agnostic; keys only on observable Bash-result
shapes, never on task content):
* ``on_before_tool`` remembers whether the pending Bash command
  backgrounds a process (a trailing ``&`` on some line).
* ``on_after_tool`` inspects each Bash result for the
  background-death / zombie signature and maintains a small rolling
  count of how many recent Bash results carried it.
* When the count crosses ``threshold`` it arms a one-shot corrective
  advisory explaining (a) zombies are already dead and cannot be
  killed — stop trying, and (b) to make a background process survive a
  Bash tool call it must be detached (``setsid`` / ``nohup ... &
  disown``) with stdio redirected, then verified in a *separate*
  subsequent Bash call.
* ``on_before_model`` injects the advisory as a single user message
  (replacing a trailing user message when present to satisfy the +1
  message contract). Fires at most once per distinct spiral, re-arming
  only after the signature clears and recurs, with an escalated second
  message if the agent keeps spiralling.

It NEVER blocks, rewrites, or terminates a tool call, so it cannot kill
a task that is legitimately backgrounding a service and moving on.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# A backgrounded child killed at tool-return shows up as SIGKILL(137) or
# SIGTERM(143), usually with no captured output.
_EXIT_SIG_RE = re.compile(r"exit\s+(?:137|143)\b")
# Zombie processes in a ps listing.
_DEFUNCT_RE = re.compile(r"<defunct>")
# The pending command backgrounds something: a line ending in a bare '&'
# (not '&&'). Conservative: only counts '&' that is the last non-space
# char on its line.
_BACKGROUND_RE = re.compile(r"(?<![&>])&\s*$", re.MULTILINE)

_ADVISORY_FIRST = (
    "RUNTIME NOTE (sandbox behaviour, not a task hint): the shell results "
    "you are seeing indicate two things you appear to be misreading.\n"
    "1. `<defunct>` / zombie processes are ALREADY DEAD. `kill`/`pkill` can "
    "never remove a zombie — it is only waiting to be reaped by its parent "
    "and consumes no resources. Stop trying to kill them; they are not "
    "blocking anything.\n"
    "2. `(exit 137 ...)` / `(exit 143 ...)` with no output usually means a "
    "process you started with a trailing `&` was torn down when the Bash "
    "call returned. Each Bash call runs in its own short-lived shell, so a "
    "plain `cmd &` does NOT survive to the next call. To keep a background "
    "service (a proxy, server, port-forward, etc.) alive across calls, "
    "DETACH it and redirect its stdio, e.g. "
    "`setsid cmd >/tmp/x.log 2>&1 < /dev/null &` or "
    "`nohup cmd >/tmp/x.log 2>&1 & disown`. Then, in a SEPARATE Bash call, "
    "verify it is actually listening/running before relying on it. Change "
    "approach now instead of repeating the kill/restart cycle."
)

_ADVISORY_REPEAT = (
    "STOP the kill/restart cycle. As noted, zombie (`<defunct>`) processes "
    "cannot be killed and are harmless, and a plain `cmd &` is torn down "
    "when the Bash call ends. Do NOT issue another `kill`/`pkill` against "
    "defunct processes. Instead, in ONE command: start the needed "
    "background process detached with stdio redirected "
    "(`setsid ... >/tmp/log 2>&1 </dev/null &`), then in your NEXT command "
    "check it is listening; if a piece truly cannot be made to work, move "
    "on to the remaining required outputs of the task."
)

_MAX_SCAN_CHARS = 6000


class BackgroundProcessAdvisorProcessor(MultiHookProcessor):
    """Advise the agent when it misreads background-process death / zombies."""

    _singleton_group = "tmax_background_process_advisor"
    _order = 7

    def __init__(
        self,
        threshold: int = 3,
        tool_name: str = "Bash",
    ) -> None:
        # Number of recent Bash results carrying the background-death /
        # zombie signature before we intervene. >=2 so a single transient
        # SIGKILL never triggers advice.
        self.threshold = max(2, int(threshold))
        self.tool_name = str(tool_name)
        self._signature_count: int = 0
        self._backgrounded_last: bool = False
        self._fired_at: int = 0
        self._pending: str = ""

    def _reset(self) -> None:
        self._signature_count = 0
        self._backgrounded_last = False
        self._fired_at = 0
        self._pending = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == self.tool_name:
            inp = event.tool_input or {}
            cmd = inp.get("command", "") if isinstance(inp, dict) else str(inp)
            self._backgrounded_last = bool(_BACKGROUND_RE.search(str(cmd)))
        yield event

    def _has_signature(self, text: str) -> bool:
        scan = text[:_MAX_SCAN_CHARS]
        # A background-death exit code is only meaningful evidence of the
        # detach problem when the command actually backgrounded something.
        if self._backgrounded_last and _EXIT_SIG_RE.search(scan):
            return True
        # Zombie processes in a ps listing indicate the kill/restart spiral
        # regardless of the current command.
        if _DEFUNCT_RE.search(scan):
            return True
        return False

    async def on_after_tool(self, event: ToolResultEvent):
        if event.tool_name != self.tool_name:
            yield event
            return

        result = event.result if event.error is None else f"ERR:{event.error}"
        text = result if isinstance(result, str) else str(result)

        if self._has_signature(text):
            self._signature_count += 1
        else:
            # Signature cleared -> allow re-arming on a fresh spiral.
            self._signature_count = 0
            self._fired_at = 0

        if (
            self._signature_count >= self.threshold
            and (self._signature_count - self._fired_at) >= self.threshold
        ):
            escalate = self._fired_at > 0
            self._pending = _ADVISORY_REPEAT if escalate else _ADVISORY_FIRST
            self._fired_at = self._signature_count

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending:
            yield event
            return
        advisory = self._pending
        self._pending = ""
        msgs = list(event.messages)
        # +1 message contract: never create two consecutive user messages.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=advisory)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=advisory),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
