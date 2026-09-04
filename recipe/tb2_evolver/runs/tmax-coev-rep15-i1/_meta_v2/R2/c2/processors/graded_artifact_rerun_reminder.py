# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""GradedArtifactRerunReminderProcessor.

Closes a structural failure mode observed on tmax / terminal-bench-2 tasks
whose success criterion is the *behaviour of an executable deliverable when an
external grader re-runs it from scratch* — a daemon, monitor, service, or
pipeline script the task says will be "run", "executed", "started", or
"tested" by the automated verifier.

Observed shape (2+ tasks this round, all `exit_reason=done`,
`finished=no_tool_calls`, `reward=0`):

* The agent authors the required executable artifact and tests it — but under a
  self-chosen invocation whose process ordering / startup timing differs from
  how the grader will restart it. The ad-hoc test passes by luck; the grader's
  clean fresh-process restart exposes a latent bug.
  - task_000118: monitor daemon exits on its first loop iteration ("no
    worker_sim.py found") when the grader starts it 0.5 s BEFORE the workload,
    so it never truncates; peak log dir hit the full 200 MB. The agent's own
    test ran monitor + deployment in one shell where import-lag masked the bug.
  - task_000140: pipeline leaves lingering `vm_service` processes when the
    grader re-runs it; the agent's follow-up only re-listed output files and
    never re-ran the pipeline from a clean process baseline + `pgrep`.

The agent has one tool (``Bash``) and no visibility into the grader's exact
invocation (verifier files are injected after the agent exits). This processor
supplies the missing discipline as a *mechanical, one-shot* nudge delivered
exactly at the exit-intent turn: it fires only when the agent's own Bash
activity shows it created an executable deliverable AND the task text describes
that deliverable being run/executed by an automated check. It reminds the agent
to re-run the artifact ONCE from a clean, fresh-process baseline (kill leftover
background processes it spawned, reset the mutable working state) using the
exact invocation contract the task describes — restarting from scratch, with
worst-case startup timing — and to confirm the *end-state metric*, not merely
that files exist.

This is a *class* fix, not a task fix: it keys off the agent's own Bash
activity and generic task vocabulary, never off task identifiers.
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


# Signals in the agent's Bash commands that it AUTHORED an executable
# deliverable (wrote a script/daemon and/or launched it as a persistent
# process). Intentionally broad: a false positive only costs one cheap
# re-run reminder; a false negative re-introduces the silent grader-mismatch.
_ARTIFACT_SIGNAL_RE = re.compile(
    r"""
      (?:cat\s*>\s*\S+\.(?:py|sh|go|rb|pl|js))     # heredoc-writing a script
    | (?:tee\s+\S+\.(?:py|sh|go|rb|pl|js))          # tee-writing a script
    | (?:chmod\s+\+x\b)                             # making something executable
    | (?:python3?\s+\S+\.(?:py))                    # running a python script
    | (?:bash\s+\S+\.(?:sh))                        # running a shell script
    | (?:\./\S+\.(?:sh|py))                         # ./script.sh
    | (?:nohup\b|&\s*$|&\s*\n)                       # backgrounded process
    | (?:\.service\b|systemctl\b|supervisor)        # service unit / supervisor
    """,
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)

# Signals in the TASK TEXT that the deliverable is graded by RE-EXECUTION
# (an automated check will run/start/test the artifact from scratch), so a
# clean fresh-restart re-run is the correct final check.
_RERUN_GRADED_RE = re.compile(
    r"""
      (?:\bdaemon\b|\bmonitor\b|\bservice\b|\bpipeline\b|\bsupervisor\b)
    | (?:run\s+(?:the\s+)?(?:deployment|simulator|script|pipeline|server|service))
    | (?:\bbackground\b.*\b(?:process|server|service|script)\b)
    | (?:will\s+(?:be\s+)?(?:run|executed|started|launched|tested|invoked))
    | (?:when\s+(?:executed|run|started|launched))
    | (?:in\s+a\s+continuous\s+loop)
    | (?:start\b.*\bstop\b)                           # start-then-stop lifecycle
    | (?:\bgracefully\s+(?:stop|shut)\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_RERUN_TOOL = "_graded_artifact_rerun_reminder"
_RERUN_ACK = "Clean re-run check acknowledged. See the message above."

_RERUN_MSG = """\
[GradedArtifactReRunCheck] This task's success is judged by an automated check \
that will RE-RUN your deliverable from scratch — as a fresh process, in its own \
invocation order and timing, not the exact shell session you tested it in. A \
solution that only worked in your own hand-rolled test can still fail the \
grader if it has a startup-ordering, timing, or leftover-state bug your test \
happened to avoid.

Before you finish, do ONE clean re-run that mirrors how the grader will invoke \
your artifact:

1. Reset to a clean baseline: kill any background processes YOU started \
(`pkill -f <your_artifact>` / `kill` the PIDs you launched) and reset the \
mutable working state the artifact operates on (empty the output/log dir, \
remove stale files) so nothing from your earlier tests is masking a bug.
2. Re-derive the grader's likely invocation from the task text: does it start \
your artifact BEFORE the workload (worst-case startup race)? Does it restart \
the workload multiple times? Start each piece as a separate fresh process in \
that order — do not rely on your artifact being "already warm" from a prior run.
3. Run it that way and confirm the END-STATE METRIC the task actually specifies \
(e.g. peak resource footprint stayed under the stated limit, no leftover/misnamed \
processes remain via `pgrep`/`ps`, the required output exists AND is correct), \
not merely that a file was created.

If this clean re-run reveals a different result than your earlier test, fix the \
artifact (e.g. guard the startup so it does not exit before the workload begins, \
make cleanup idempotent across repeated runs) and re-run until the end-state \
metric holds. Only then finish."""


class GradedArtifactReRunReminderProcessor(MultiHookProcessor):
    """One-shot exit-intent nudge to re-run a graded executable artifact clean.

    Fires at most once per task, and only when (a) the agent's Bash activity has
    matched an executable-deliverable signal AND (b) the task text describes that
    deliverable being run/executed by an automated check. Coordinates with the
    other exit-intent processors (self-verify, service-deps reminder) by only
    acting on a turn that still has no tool calls: if another processor has
    already converted the current exit attempt into a keepalive tool call, this
    processor stays silent and fires on the next genuine exit attempt instead.
    """

    _singleton_group = "graded_artifact_rerun_reminder"
    # Run after CustomSelfVerifyProcessor (_order=90) and ServiceDepsReminder
    # (_order=91) so the exit-intent hooks serialize rather than all rewriting
    # tool_calls on the same turn.
    _order = 92

    def __init__(self) -> None:
        self._task_text = ""
        self._task_graded_by_rerun = False
        self._artifact_seen = False
        self._reminded = False
        self._pending_message = ""

    def _capture_task_text(self, event: TaskStartEvent) -> None:
        # Task description is exposed under a few attribute names across
        # harness versions; probe defensively and fall back to str(event).
        text = ""
        for attr in ("task_description", "description", "instruction",
                     "prompt", "task"):
            val = getattr(event, attr, None)
            if isinstance(val, str) and val:
                text = val
                break
        if not text:
            try:
                text = str(getattr(event, "task", "") or "")
            except Exception:
                text = ""
        self._task_text = text
        self._task_graded_by_rerun = bool(_RERUN_GRADED_RE.search(text))

    async def on_task_start(self, event: TaskStartEvent):
        self._task_text = ""
        self._task_graded_by_rerun = False
        self._artifact_seen = False
        self._reminded = False
        self._pending_message = ""
        try:
            self._capture_task_text(event)
        except Exception:
            self._task_graded_by_rerun = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Absorb our own keepalive tool call so it never reaches the sandbox.
        if event.tool_name == _RERUN_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_RERUN_ACK
            )
            return
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") or ""
            if _ARTIFACT_SIGNAL_RE.search(cmd):
                self._artifact_seen = True
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
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if (
            exit_intent
            and self._task_graded_by_rerun
            and self._artifact_seen
            and not self._reminded
        ):
            self._reminded = True
            self._pending_message = _RERUN_MSG
            keepalive = ToolCall(
                id=f"rerun-{uuid.uuid4().hex[:8]}",
                name=_RERUN_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._task_text = ""
        self._task_graded_by_rerun = False
        self._artifact_seen = False
        self._reminded = False
        self._pending_message = ""
        yield event
