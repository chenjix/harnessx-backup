"""TeardownEndStateGuard — arm a clean-teardown check only on lifecycle tasks
that actually launched a background process.

Problem (harness mechanism bias)
---------------------------------
The stock TB2 self-verify checklist (CustomSelfVerifyProcessor, injected once
per task right before the agent exits) is one-sided on service lifecycle:

    5. **For running services:** confirm they are still alive and reachable
       right now, not just that they started earlier.

That steering is correct when the verifier connects to a service the agent
must leave running, but it is *actively wrong* for lifecycle / init-script /
CI-CD-pipeline tasks whose success criterion is a CLEAN TEARDOWN — no lingering
processes at exit. On those tasks the agent starts a service (its own testing,
or by running the pipeline it authored) and, nudged only toward "keep it
alive", never checks for or reaps stray processes before exiting. The verifier
then finds lingering PIDs and the task scores 0 despite a functionally correct
solution.

Design — narrow arming, not a blanket checklist edit
----------------------------------------------------
Two independent gates must BOTH be true before this guard fires, so the ~all
non-teardown tasks (including services that must stay alive) are never touched:

  Gate 1 (task intent): the task description contains explicit teardown /
    graceful-stop / clean-shutdown language (e.g. "gracefully stop",
    "no lingering", "terminate", "SIGTERM", "shut down", "kill", "clean up",
    "lifecycle", "teardown", "stop the service"). A task that never asks for a
    shutdown never arms.

  Gate 2 (agent behavior): during the run the agent actually issued a Bash
    command that launches a background/daemon process (trailing ``&``, nohup,
    disown, setsid, systemctl/service start, or common server launchers). If
    the agent never started anything in the background there is nothing to
    reap and the guard stays silent.

When both gates are satisfied, on the self-verify checkpoint (identified by the
stock checklist sentinel, injected exactly once by CustomSelfVerifyProcessor)
this guard augments *that same last user message* with one concrete teardown
verification step. It does NOT insert a new message — it edits the content of
the last user message only, the sole message mutation the on_before_model
contract permits when the last message is a user message. Fires at most once
per task and is idempotent (guarded by its own marker sentinel).

Generality: the added guidance is a strategy (verify the end state you were
asked to reach), not a task-specific recipe. No task ids, ports, process names,
or file paths appear here. It helps any unseen lifecycle task that asks for a
graceful shutdown after launching a background process.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    TaskStartEvent,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Substring that uniquely identifies the stock self-verify checklist message
# (CustomSelfVerifyProcessor._SELF_VERIFY_MSG). A stable phrase, not a literal.
_SELF_VERIFY_SENTINEL = "run through this checklist"

# Idempotency marker so the item is never appended twice.
_MARKER = "[end-state teardown check]"

# Gate 1: the task explicitly asks for a shutdown / teardown end state.
_TEARDOWN_INTENT = re.compile(
    r"gracefully\s+stop"
    r"|graceful\s+shut"
    r"|no\s+lingering"
    r"|clean\s*(?:ly)?\s*(?:shut|stop|teardown|tear\s*down|exit)"
    r"|tear\s*down"
    r"|teardown"
    r"|shut\s*down"
    r"|shutdown"
    r"|sigterm"
    r"|sigkill"
    r"|\bkill\b"
    r"|stop\s+the\s+service"
    r"|stop\s+the\s+server"
    r"|terminate"
    r"|life\s*cycle"
    r"|lifecycle",
    re.IGNORECASE,
)

# Gate 2: a Bash command that launches something in the background / as a daemon.
_BG_LAUNCH = re.compile(
    r"&\s*$"                       # trailing background operator
    r"|&\s*(?:echo|sleep|wait|disown|>)"  # backgrounded then chained
    r"|\bnohup\b"
    r"|\bdisown\b"
    r"|\bsetsid\b"
    r"|systemctl\s+start"
    r"|service\s+\S+\s+start"
    r"|start[_-]service"
    r"|\buvicorn\b"
    r"|\bgunicorn\b"
    r"|flask\s+run"
    r"|http\.server"
    r"|ListenAndServe"
    r"|\bserve\b",
    re.IGNORECASE,
)

_TEARDOWN_ITEM = (
    "\n\n"
    + _MARKER
    + "\n**Clean up processes you started.** This task asks for a graceful "
    "shutdown / clean end state, and you launched a process in the background "
    "during your work (your own testing, or by running the pipeline you "
    "wrote). The verifier will fail if any such process is still running when "
    "you finish. Before you exit: list the processes you started (e.g. by "
    "matching the binary or command name), send the stop signal the task "
    "specifies, wait a moment, then re-list to confirm NONE survive — escalate "
    "the signal if a stray remains. Only skip teardown for a service the task "
    "explicitly wants left alive and reachable."
)


class TeardownEndStateGuard(MultiHookProcessor):
    """Append a clean-teardown verification item to the self-verify checklist,
    but only on teardown-intent tasks where the agent backgrounded a process."""

    _singleton_group = "tb2_teardown_end_state_guard"
    _order = 92  # after CustomSelfVerifyProcessor (_order=90)

    def __init__(self) -> None:
        self._intent = False       # gate 1: task wants a teardown
        self._launched_bg = False  # gate 2: agent backgrounded a process
        self._applied = False

    async def on_task_start(self, event: TaskStartEvent):
        desc = event.task_description or ""
        self._intent = bool(_TEARDOWN_INTENT.search(desc))
        self._launched_bg = False
        self._applied = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if not self._launched_bg and event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") if event.tool_input else ""
            if isinstance(cmd, str) and _BG_LAUNCH.search(cmd):
                self._launched_bg = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        msgs = event.messages
        if self._applied or not self._intent or not self._launched_bg or not msgs:
            yield event
            return

        last = msgs[-1]
        if last.role != "user":
            yield event
            return

        content = last.content
        if not isinstance(content, str) or _SELF_VERIFY_SENTINEL not in content:
            yield event
            return
        if _MARKER in content:
            self._applied = True
            yield event
            return

        new_last = dataclasses.replace(last, content=content + _TEARDOWN_ITEM)
        self._applied = True
        yield dataclasses.replace(event, messages=msgs[:-1] + (new_last,))

    async def on_task_end(self, event):
        self._intent = False
        self._launched_bg = False
        self._applied = False
        yield event
