# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""BackgroundStateReconcileProcessor.

A TB2 control processor that closes a structural blind spot in the
final-state verification path: the agent's *own* testing actions mutate
the container state the verifier later inspects. Concretely, tasks that
involve service/daemon lifecycles frequently have the agent start a
background process (e.g. ``./service &``, ``nohup ...``, a ``start_*.sh``
launcher) while testing, then declare done without reconciling whether
the task wants that process to *persist* or to be *cleanly shut down*.

Two opposite grader polarities exist in this benchmark family:

* some graders require a service to still be *alive and reachable* after
  the agent exits (the container is kept alive for the verifier);
* other graders require *no lingering* service processes in the final
  state (e.g. a ``pgrep -f <svc>`` must return empty).

The existing self-verify checklist nudges only toward the first polarity
("confirm services are still alive"), which is actively wrong for the
second. This processor adds a single, one-shot, *neutral* reconciliation
prompt — it does NOT kill anything and does NOT assume a polarity. It
only fires when the session actually launched a background process and
the agent is trying to exit, asking it to reconcile the live process
state against the task's stated success criteria.

Design / composition notes
---------------------------
* ``_order = 91`` places this strictly after ``CustomSelfVerifyProcessor``
  (``_order = 90``). On the first exit-intent turn the self-verify
  processor rewrites the model response to a keepalive tool call, so
  ``event.tool_calls`` is non-empty by the time this processor runs and
  it stays silent. It therefore only fires on a *later* genuine
  no-tool-call exit, i.e. after the standard checklist has already run.
* The nudge is injected exactly once per task run, following the same
  keepalive + deferred-user-message pattern the self-verify processor
  uses, so the message-count contract stays at +1 user message.
* No task IDs, paths, service names, or other task-specific literals are
  embedded — detection is by generic shell-launch / cleanup patterns.
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
)
from harnessx.core.processor import MultiHookProcessor

_RECONCILE_TOOL = "_tb2_bg_state_reconcile"
_RECONCILE_ACK = "Background-state reconciliation initiated. See the message above."

# Commands that start a long-lived process in the background.
_LAUNCH_RE = re.compile(
    r"""
    (?:\bnohup\b)                         # nohup ...
    | (?:\bdisown\b)                      # ... & disown
    | (?:\bsetsid\b)                      # setsid ...
    | (?:&\s*$)                           # trailing & (backgrounded)
    | (?:&\s*(?:echo|>|>>))               # backgrounded then chained
    | (?:\b\S*start\S*\.sh\b)             # start*.sh launcher scripts
    | (?:\b\S*_service\b)                 # *_service binaries
    | (?:\bsystemctl\s+start\b)
    | (?:\bservice\s+\S+\s+start\b)
    """,
    re.VERBOSE,
)

# Commands that stop / reap background processes.
_CLEANUP_RE = re.compile(
    r"\b(?:kill|pkill|killall|systemctl\s+stop|service\s+\S+\s+stop)\b"
)

_RECONCILE_MSG = """\
Final background-process reconciliation — do this before you exit.

You started one or more processes in the background during this task
(e.g. a service, daemon, or a launcher/pipeline script). The automated
verifier inspects the container's final state, so any process you left
running while testing is part of what it will see.

1. List what is actually running right now, e.g.:
```bash
ps aux | grep -v grep | grep -iE 'your-service-or-binary-name'
```

2. Re-read the task's success criteria and decide the REQUIRED end state:
   - If the task wants the service left running/reachable, confirm it is
     alive and responding right now (not just that it started earlier).
   - If the task wants a clean shutdown / "no lingering processes"
     (e.g. a pipeline that must start AND then stop the service, or a
     test that asserts no matching process remains), make sure every
     instance you started — including ones from your own test runs — is
     terminated, and re-check with `ps`/`pgrep` that none remain.

3. Do not guess the polarity: match it to what the task text asks for.

Fix the process state if it does not match, then finish.\
"""


class BackgroundStateReconcileProcessor(MultiHookProcessor):
    """One-shot exit nudge to reconcile leftover background processes.

    Fires at most once per task run, and only when the session both
    (a) launched a background process and (b) reaches a genuine
    no-tool-call exit intent after the standard self-verify checklist.
    """

    _singleton_group = "tb2_bg_state_reconcile"
    _order = 91  # strictly after CustomSelfVerifyProcessor (_order=90)

    def __init__(self) -> None:
        self._launched = False
        self._fired = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._launched = False
        self._fired = False
        self._pending_message = ""
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Detect background-launch commands without acting on them.
        if event.tool_name == "Bash":
            cmd = (event.tool_input or {}).get("command", "") or ""
            if _LAUNCH_RE.search(cmd) and not _CLEANUP_RE.search(cmd):
                self._launched = True
        # Serve the synthetic reconciliation "tool" as a no-op ack.
        if event.tool_name == _RECONCILE_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_RECONCILE_ACK
            )
        else:
            yield event

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if exit_intent and self._launched and not self._fired:
            self._fired = True
            self._pending_message = _RECONCILE_MSG
            keepalive = ToolCall(
                id=f"bgr-{uuid.uuid4().hex[:8]}",
                name=_RECONCILE_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        # Last message is a tool result (role != user) -> append exactly +1 user.
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._launched = False
        self._fired = False
        self._pending_message = ""
        yield event
