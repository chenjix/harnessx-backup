# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""FinalStateHygieneProcessor — a Control-lever exit-time reminder for TB2.

Terminal-Bench 2 verifiers frequently assert on the *final container state*
rather than only on output-file contents. A recurring failure shape is the
"final-state process/resource hygiene" gap: the agent starts a background
service (or spawns worker/monitor processes) while implementing and testing,
declares the task done, and exits — but leaves behind processes, ports, or
resource growth in a state the verifier rejects. Two orthogonal sub-shapes:

  * **Lingering processes** — the task asks for a service to be *started and
    then gracefully stopped* (or never left running), but copies of the
    service spawned during the agent's own manual testing are still alive at
    exit. The verifier greps for the process name and fails on any survivor.
    (`kill -TERM $PID` on the last recorded PID is racy and misses earlier
    invocations.)

  * **Wrong end-state** — the task asks for a *named* process to be running
    (correct `/proc/<pid>/comm`), or for a bounded resource footprint, and the
    agent's implementation leaves it in the wrong shape.

The stock ``CustomSelfVerifyProcessor`` checklist covers output files and
"is the service still alive", but says nothing about *cleanup* or *process
identity / final state*. This processor closes that gap generically: whenever
the exit-time self-verify handshake fires, it appends a short, task-agnostic
process-hygiene checklist to the acknowledgement the agent reads. It injects
NO new messages and never touches message counts — it only appends text to the
existing synthetic ``_tb2_self_verify`` tool result, exactly like the stock
``CustomEditToolProcessor`` appends to a Bash result. That keeps the hook
contract trivially satisfied.

Scope note: this is a *strategy* reminder, not task knowledge. It never names a
specific service, PID, path, or command — it tells the agent to reconcile the
final process/resource state against the task's stated end-state, which
generalises to any service/daemon/worker task the agent has never seen.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Must match the tool name the stock CustomSelfVerifyProcessor uses for its
# exit-time keepalive handshake. Kept as a module constant so a rename upstream
# is a one-line fix here.
_SELF_VERIFY_TOOL = "_tb2_self_verify"

_HYGIENE_REMINDER = """

---
Final-state process & resource hygiene (do NOT skip if this task involved starting, testing, or managing any service, daemon, or background worker):

A. Reconcile the RUNNING process state against what the task asks for as the END state:
   - If the task wants the service STOPPED / gracefully shut down at the end, confirm there are ZERO surviving copies now — including any you spawned while manually testing. List them explicitly, e.g. `pgrep -af <service-name>` or `ps aux | grep <service>`, then terminate every stray PID (a single `kill -TERM <last-pid>` is racy and misses earlier test launches; loop until `pgrep` returns nothing, escalating to `kill -9` if needed). Re-check with `pgrep` and confirm empty.
   - If the task wants a process LEFT RUNNING, confirm exactly the intended process is alive AND has the identity the task implies (e.g. the right program name in `/proc/<pid>/comm`, the right listening port via `ss -ltnp` / `lsof -i`). `exec`-ing the real binary rather than leaving a wrapper `bash` in front of it is often what a name/identity check expects.

B. Reconcile RESOURCE state: if the task bounds a footprint (log/dir size, memory, leaked bytes, open files), measure it now with the same units the requirement states and confirm you are within the limit — background workers left running can push it over after you exit.

Fix any mismatch before finishing. Your own build/test runs count toward the final state the verifier inspects.
"""


class FinalStateHygieneProcessor(MultiHookProcessor):
    """Append a task-agnostic final-state process/resource hygiene checklist to
    the exit-time self-verify acknowledgement. Fires at most once per task."""

    _singleton_group = "tb2_final_state_hygiene"
    # Run after the stock self-verify processor (order 90) so its synthetic
    # result exists before we append to it.
    _order = 95

    def __init__(self, self_verify_tool: str = _SELF_VERIFY_TOOL) -> None:
        self._tool_name = self_verify_tool
        self._fired = False

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if event.tool_name == self._tool_name and not self._fired:
            self._fired = True
            new_result = (event.result or "") + _HYGIENE_REMINDER
            yield dataclasses.replace(event, result=new_result)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        yield event
