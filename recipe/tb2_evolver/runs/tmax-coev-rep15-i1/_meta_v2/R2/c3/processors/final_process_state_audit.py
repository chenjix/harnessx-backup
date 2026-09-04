# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""FinalProcessStateAuditProcessor.

Closes a structural failure mode on Terminal-Bench-2 / tmax *service* tasks:
the verifier asserts on the container's final **process** state (e.g.
``pgrep -f <service>`` must return nothing after a "start it, hit it, then
gracefully stop it" pipeline), but the agent only ever reconciles output
*files* against the task and exits believing it is done. The stock
``CustomSelfVerifyProcessor`` checklist is file/format oriented and the model
narrates straight past the weak "final state is clean" clause, so lingering
background services survive into the verifier phase and score the task 0.

Observed archetype (task_000140): the agent fixes a Go service, writes a
supervisor script and a CI/CD pipeline that spawns ``./vm_service &`` and
issues a single ``kill -TERM $(cat service.pid)``. Because the pipeline is run
repeatedly (agent + verifier re-runs) and the single-PID kill is racy, multiple
``vm_service`` processes are left running; the verifier fails
``test_no_lingering_service_processes`` with a list of lingering PIDs. The agent
never ran ``pgrep`` / ``ps`` to confirm the required stopped end-state.

The fix is a *mechanical, one-shot* escalation from "remind the agent" to
"show the agent the ground truth": when the agent has demonstrably stood up a
**backgrounded** service and then tries to finish, this processor injects one
REAL ``Bash`` snapshot of the live process / listening-socket / job state so the
lingering processes appear as an actual tool result the model cannot skim past,
then appends a single reconciliation instruction: match the running processes
and bound ports against the task's required end-state (stop them if the task
asked for a graceful shutdown / stopped service; leave them running if the task
asked for a persistent daemon).

Task-agnostic by construction:
* fires only on the agent's own Bash-activity signal (backgrounded process),
  never on task identifiers;
* the snapshot commands (``ps``, ``ss``, ``jobs``) name no task-specific
  process, port, or path;
* it never kills anything itself (that could regress a task that wants a
  service left running) — it surfaces state and lets the agent reconcile;
* it fires at most once per task and then always yields a free exit, so a
  genuinely-finished run is never trapped.
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


# The agent has spawned / manages a *backgrounded* service whose liveness the
# verifier is likely to assert on. Intentionally broad; a false positive costs
# one cheap read-only snapshot + one reminder, a false negative re-introduces
# the silent lingering-process failure.
_BG_SERVICE_RE = re.compile(
    r"""
      (?:&\s*$)                          # command backgrounded with trailing &
    | (?:&\s*(?:echo|sleep|disown))      # backgrounded then follow-up
    | (?:\bnohup\b)                      # nohup daemon
    | (?:\bsetsid\b)                     # detached session
    | (?:\bdisown\b)                     # detached job
    | (?:\.pid\b)                        # writes/reads a pid file
    | (?:\$!)                            # captures last background pid
    | (?:\bsystemctl\b\s+start)          # service manager start
    | (?:\bservice\b\s+\S+\s+start)      # sysv service start
    | (?:ListenAndServe|http\.Serve)     # go http server
    | (?:\blisten\s*\()                  # C/C++ socket listen()
    | (?:http\.server|BaseHTTPServer)    # python stdlib http servers
    | (?:flask|gunicorn|uvicorn|fastapi) # python web frameworks
    | (?:127\.0\.0\.1:\d+)               # binding/probing a local port
    | (?:localhost:\d+)
    | (?:0\.0\.0\.0:\d+)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Task text asking for the service to end up STOPPED (graceful shutdown) — used
# only to sharpen the reconcile wording; the snapshot fires either way.
_STOP_INTENT_RE = re.compile(
    r"(graceful\w*\s+(?:stop|shut\s*down|shutdown|terminat)"
    r"|shut\s*down|shutdown"
    r"|\bstop(?:s|ping|ped)?\b\s+the\s+\S+"
    r"|SIGTERM|kill\s+-?TERM|kill\s+-?15"
    r"|clean(?:ly)?\s+(?:exit|terminat|stop)"
    r"|leave(?:s)?\s+no\s+.*(?:process|service)\s+running)",
    re.IGNORECASE,
)

_AUDIT_TOOL = "Bash"

# Read-only ground-truth snapshot. No redirects that write files; every branch
# is guarded with `|| true` so a missing tool never errors the turn.
_SNAPSHOT_CMD = (
    "echo '=== running processes (non-kernel) ===';"
    " ps -eo pid,ppid,comm,args --sort=comm 2>&1 | grep -v -E '\\[' || true;"
    " echo '=== listening sockets ===';"
    " (ss -ltnp 2>&1 || netstat -ltnp 2>&1) || true;"
    " echo '=== background jobs in this shell ===';"
    " jobs -l 2>&1 || true"
)

_RECONCILE_STOP = """\
[FinalProcessStateAudit] The snapshot above is the ground-truth process / \
listening-socket state your container is in RIGHT NOW — this is what the \
verifier will inspect after you exit. This task requires the service to end up \
STOPPED (a graceful shutdown / clean final state). Before finishing:

1. Read the snapshot. If ANY process you started (or its port) is still present, \
your kill did not fully take effect — a common cause is a single-PID kill that \
misses copies spawned by repeated pipeline runs, or a `kill` that did not wait \
for the process to actually die.
2. Terminate ALL matching instances by pattern, not by one recorded PID — e.g. \
`pkill -TERM -f <your-service-name>` then, after a short wait, escalate to \
`pkill -KILL -f <your-service-name>` for any survivor. Also make your pipeline \
robust to being re-run (idempotent start/stop) since the verifier may run it \
again.
3. Re-run the snapshot to CONFIRM zero matching processes remain before you \
declare done.

Do this now with real Bash commands; do not just assert it is clean."""

_RECONCILE_KEEP = """\
[FinalProcessStateAudit] The snapshot above is the ground-truth process / \
listening-socket state your container is in RIGHT NOW — this is what the \
verifier will inspect after you exit. Reconcile it against the task's required \
END state:

* If the task wants a service left RUNNING/reachable, confirm exactly the right \
process is up and listening on the right port in the snapshot, and that it is \
backgrounded so it survives your exit (not a foreground/soon-to-die child).
* If the task wants the service STOPPED/cleaned up, ensure NO matching process \
or port survives — terminate every matching instance by pattern \
(`pkill -f <name>`), not by a single recorded PID, then re-snapshot to confirm.

Do this now with real Bash commands; do not just assert the state is correct."""


class FinalProcessStateAuditProcessor(MultiHookProcessor):
    """One-shot, exit-time REAL process-state snapshot + reconcile nudge.

    Fires at most once per task, only when the agent's Bash activity has matched
    a backgrounded-service signal. Coordinates with the other exit-intent
    processors (self-verify _order=90, svc-deps _order=91) by running last
    (_order=96): it only acts on an exit turn that still has zero tool calls, so
    if an earlier processor already converted this exit attempt into a keepalive
    call, this processor stays silent and fires on the next genuine exit.
    """

    _singleton_group = "tb2_final_proc_audit"
    _order = 96

    def __init__(self) -> None:
        self._bg_service_seen = False
        self._stop_intent = False
        self._audited = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._bg_service_seen = False
        self._stop_intent = bool(_STOP_INTENT_RE.search(event.task_description or ""))
        self._audited = False
        self._pending_message = ""
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") or ""
            if _BG_SERVICE_RE.search(cmd):
                self._bg_service_seen = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        # Last message is the snapshot tool result (role != user) → append
        # exactly +1 user message, satisfying the hook contract.
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if exit_intent and self._bg_service_seen and not self._audited:
            self._audited = True
            self._pending_message = (
                _RECONCILE_STOP if self._stop_intent else _RECONCILE_KEEP
            )
            # Inject a REAL Bash snapshot (NOT intercepted in on_before_tool),
            # so the live process/socket state lands in context as ground truth.
            snapshot = ToolCall(
                id=f"fps-{uuid.uuid4().hex[:8]}",
                name=_AUDIT_TOOL,
                input={"command": _SNAPSHOT_CMD},
            )
            yield dataclasses.replace(event, tool_calls=(snapshot,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._bg_service_seen = False
        self._stop_intent = False
        self._audited = False
        self._pending_message = ""
        yield event
