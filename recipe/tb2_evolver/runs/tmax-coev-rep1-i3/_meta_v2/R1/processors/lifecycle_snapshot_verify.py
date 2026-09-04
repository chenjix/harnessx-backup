# SPDX-License-Identifier: MIT
"""Exit-time self-verification with a *mechanical* process/pid ground-truth snapshot.

Motivation
----------
The stock ``CustomSelfVerifyProcessor`` (and the R0
``ProcessLifecycleSelfVerifyProcessor`` that extended it) inject a *textual*
checklist asking the agent to reconcile the final process table and confirm
any recorded PID names the real program. On process-management /
background-service tasks the model reliably *narrates* compliance ("Service
stopped", "PID 578 is the actual monitor process") without actually
re-inspecting the live state — and the verifier then fails on:

  * lingering service processes the agent spawned while testing
    (verifier greps ``pgrep -f <svc>`` and finds survivors), and
  * a ``*.pid`` / ``pid.txt`` file that contains a wrapper PID or, worse,
    multiple newline-separated PIDs (``echo $! > pid.txt`` accumulated across
    repeated launches), so the verifier's ``pid_str.isdigit()`` check fails.

A text reminder is demonstrably insufficient: the agent reads it and
self-reports success. What the model cannot hand-wave past is *raw tool
output it did not author*. So instead of only injecting a checklist, this
processor injects a **real ``Bash`` diagnostic tool call** on the agent's
first exit-intent turn. The run loop executes it and feeds the real snapshot
back into context: the current user-process table plus the contents of every
``*.pid`` / ``pid.txt`` file under the working directory. The follow-up
checklist message then asks the agent to reconcile *that concrete output*
with the task's stated lifecycle requirements before committing to exit.

Generality
----------
The diagnostic command contains **no task ids, paths, ports, or process
names** from any training task — it enumerates whatever is actually running
and whatever pid files actually exist, letting the agent fill in the concrete
lifecycle requirements from the task description it re-reads. The snapshot is
a general shell-lifecycle discipline (single-instance services, correct PID
recording, no leftover test processes), not domain knowledge. It fires at
most once per task, so it adds no per-step mechanical regression surface and
costs one extra Bash round-trip only on runs that reach exit intent.

Wiring
------
Reuses the stock singleton group ``tb2_self_verify`` so it *replaces* the
stock / R0 self-verify processor rather than firing a second, conflicting
keepalive.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskStartEvent,
    TaskEndEvent,
    ToolCall,
)

from benchmarks.terminal_bench_2.harness import (
    CustomSelfVerifyProcessor,
    _SELF_VERIFY_MSG,
)


# A fully general lifecycle snapshot. Enumerates non-kernel user processes and
# dumps the contents of any pid files under the working dir. `2>/dev/null` and
# the `|| true` guards keep it silent/successful even when nothing matches.
_SNAPSHOT_CMD = (
    "echo '=== LIFECYCLE SNAPSHOT (auto) ==='; "
    "echo '--- running processes (user, non-kernel) ---'; "
    "ps -eo pid,ppid,stat,comm,args --sort=pid 2>/dev/null "
    "| awk 'NR==1 || ($2!=2 && $1!=2)' | head -n 60 || true; "
    "echo '--- pid files under working dir ---'; "
    "for f in $(find . /home/user -maxdepth 4 \\( -name '*.pid' -o -name 'pid.txt' \\) "
    "-type f 2>/dev/null | sort -u | head -n 20); do "
    "echo \"# $f:\"; cat \"$f\" 2>/dev/null; echo; done || true; "
    "echo '=== END SNAPSHOT ==='"
)

_RECONCILE_MSG = (
    "Above is a live snapshot of the actual process table and every pid file "
    "on disk — this is ground truth, not your earlier narration. Reconcile it "
    "with the task's lifecycle requirements before you finish:\n"
    "  - Every process the task requires to keep RUNNING must appear above right now.\n"
    "  - Every process that must be STOPPED / not left lingering (including any you "
    "started only to test) must be ABSENT — if you see duplicates or leftovers, kill "
    "them now.\n"
    "  - If the task records a PID in a file, that file must contain EXACTLY ONE integer "
    "naming the real target program (check `cat /proc/<pid>/comm`), not a shell wrapper "
    "and not several accumulated PIDs. If the pid file above has multiple lines or names "
    "a wrapper (bash/sh), rewrite it with the single correct PID.\n\n"
    + _SELF_VERIFY_MSG
)


class LifecycleSnapshotSelfVerifyProcessor(CustomSelfVerifyProcessor):
    """Self-verify that injects a real Bash lifecycle snapshot before the checklist.

    On the first exit-intent turn it emits a genuine ``Bash`` tool call (the
    run loop executes it) so the concrete process/pid state lands in context;
    the reconciliation + stock checklist message is then injected on the
    following ``on_before_model``.
    """

    async def on_task_start(self, event: TaskStartEvent):
        self._verified = False
        self._pending_message = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if exit_intent and not self._verified:
            self._verified = True
            # Arm the reconciliation + checklist message for the next
            # on_before_model, and inject a REAL Bash diagnostic tool call now.
            self._pending_message = _RECONCILE_MSG
            snapshot = ToolCall(
                id=f"lc-{uuid.uuid4().hex[:8]}",
                name="Bash",
                input={"command": _SNAPSHOT_CMD},
            )
            yield dataclasses.replace(event, tool_calls=(snapshot,))
        else:
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

    async def on_task_end(self, event: TaskEndEvent):
        self._verified = False
        self._pending_message = ""
        yield event
