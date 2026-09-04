# SPDX-License-Identifier: MIT
"""StuckCommandRecoveryProcessor — break repeated-failing-command loops.

Problem class (observable, task-agnostic)
------------------------------------------
A weak model frequently hits a tool call that FAILS (non-zero exit or
an error-shaped result), then re-issues a *near-identical* command that
produces the *same* failure, over and over, without changing its
approach. The loop burns the step/token budget and the run either times
out (``budget_exceeded``) or crashes (``error``) with the deliverable
still broken.

This is purely a function of observable run state:

* the tool result carries a failure marker (``event.error`` is set, or
  the result text contains ``(exit N)`` with N != 0, or a generic error
  fingerprint such as ``Traceback``/``error:``/``parse error``), and
* the *normalised command string* is the same as the previous failing
  call, and
* the *error fingerprint* is the same too (a genuinely NEW error means
  the agent is making progress and must NOT be interrupted).

No existing processor guards this: ``CustomEditToolProcessor`` counts
file over-edits (fires on write-count, ignores exit status and command
identity), and ``LengthTruncationRecoveryProcessor`` only handles
``finish_reason=length`` text loops. The gap is a *same-command,
same-error* repetition guard.

Mechanism
---------
On each real tool result the processor computes ``(cmd_sig, err_sig)``.
While consecutive results keep the same pair it counts them. When the
count reaches ``repeat_threshold`` it injects exactly ONE legible user
message that (a) names the stuck command and the recurring error, and
(b) instructs the agent to STOP repeating it and try a *different*
approach (inspect inputs, change invocation/flags, isolate the failing
piece, or reconsider the requirement). It then arms a cooldown: it will
not nudge again until the command signature changes, so a still-looping
agent is nudged at most once per distinct stuck signature, and an agent
that is making progress (different command or different error) is never
touched.

The nudge text is keyed off run state only — it contains no
task-specific literals, paths, answers, tool names, or benchmark
identifiers. It generalises to any task where the single Bash tool can
fail and be blindly retried.
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

_GATE_TOOL = "_stuck_cmd_recovery"
_GATE_ACK = "Loop-break note delivered — read the message above, then change approach."

_WS = re.compile(r"\s+")
_EXIT = re.compile(r"\(exit\s+(\d+)")
# Generic, tool-agnostic error fingerprints that show up in Bash output.
_ERR_MARKERS = (
    "traceback (most recent call last)",
    "error:",
    "error[",
    "parse error",
    "exception",
    "command not found",
    "no such file",
    "permission denied",
    "cannot ",
    "failed",
    "not found",
    "fatal:",
    "segmentation fault",
    "syntaxerror",
    "assertionerror",
)


def _norm_cmd(text: str) -> str:
    """Collapse whitespace so trivially-reformatted reruns match."""
    return _WS.sub(" ", (text or "").strip())


def _failure_fingerprint(result: str, error: str | None) -> str | None:
    """Return a stable fingerprint of the failure, or None if no failure.

    A DIFFERENT fingerprint means the agent made progress (new error) and
    must not be interrupted, so we key on the salient error lines rather
    than the whole output.
    """
    if error:
        return _norm_cmd(error)[:200]

    body = result or ""
    low = body.lower()

    exit_nonzero = False
    m = _EXIT.search(body)
    if m and m.group(1) != "0":
        exit_nonzero = True

    marker_hit = any(mk in low for mk in _ERR_MARKERS)
    if not (exit_nonzero or marker_hit):
        return None

    # Build a fingerprint from the error-looking lines (bounded), so that
    # a change in the actual error resets the streak.
    err_lines = [
        ln.strip()
        for ln in body.splitlines()
        if any(mk in ln.lower() for mk in _ERR_MARKERS)
    ]
    if not err_lines:
        # Non-zero exit with no obvious error line: fall back to the exit
        # code plus the tail of the output.
        tail = _norm_cmd(body)[-160:]
        code = m.group(1) if m else "?"
        return f"exit={code}|{tail}"
    return _norm_cmd(" | ".join(err_lines[:3]))[:200]


class StuckCommandRecoveryProcessor(MultiHookProcessor):
    """Interrupt same-command / same-error retry loops with one nudge.

    Parameters
    ----------
    repeat_threshold:
        Number of consecutive identical (command, error) failures that
        trips the nudge. Default 3 — the first failure is normal, a
        second retry can be a legitimate transient fix; a third identical
        failure is the signal that the agent is stuck.
    max_nudges:
        Hard cap on total nudges per task, so a pathological task cannot
        turn the guard into its own loop. Default 3.
    """

    _singleton_group = "tb2_stuck_cmd_recovery"
    _order = 88

    def __init__(self, repeat_threshold: int = 3, max_nudges: int = 3) -> None:
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.max_nudges = int(max_nudges)
        self._reset()

    def _reset(self) -> None:
        self._last_cmd: str | None = None
        self._last_err: str | None = None
        self._streak = 0
        self._nudges_used = 0
        self._armed_for_sig: tuple[str, str] | None = None  # signature already nudged
        self._pending_message = ""
        self._pending_cmd: str | None = None

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        # Ignore our own synthetic keepalive results.
        if event.tool_name == _GATE_TOOL:
            yield event
            return

        # ToolResultEvent carries no command; we captured it in
        # on_before_tool (self._pending_cmd). Here we only fingerprint the
        # failure of the result.
        err = _failure_fingerprint(str(event.result or ""), event.error)

        if err is None:
            # Success (or non-error output) breaks any streak.
            self._streak = 0
            self._last_err = None
            self._armed_for_sig = None
            yield event
            return

        sig_cmd = self._pending_cmd if self._pending_cmd is not None else ""
        if sig_cmd and sig_cmd == self._last_cmd and err == self._last_err:
            self._streak += 1
        else:
            self._streak = 1
            self._last_cmd = sig_cmd
            self._last_err = err

        sig = (self._last_cmd or "", self._last_err or "")
        if (
            self._streak >= self.repeat_threshold
            and self._nudges_used < self.max_nudges
            and self._armed_for_sig != sig
        ):
            self._armed_for_sig = sig
            self._nudges_used += 1
            self._pending_message = self._build_note(sig[0], sig[1])

        yield event

    # We need the command string that produced the result. ToolResultEvent
    # does not carry it, so we capture it in on_before_tool (self._pending_cmd,
    # initialised in _reset).

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _GATE_TOOL:
            # Synthetic keepalive — intercept, do not execute.
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_GATE_ACK
            )
            return
        cmd = ""
        if isinstance(event.tool_input, dict):
            cmd = _norm_cmd(str(event.tool_input.get("command", "")))
        self._pending_cmd = cmd
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        # Append exactly +1 user message (contract: +1 per synthetic chain).
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        # If a nudge is pending and the model tried to exit without tool
        # calls, keep the loop alive with a synthetic tool call so the
        # pending user message is delivered on the next before_model.
        if self._pending_message and event.finish_reason in ("end_turn", "stop") and not event.tool_calls:
            keepalive = ToolCall(
                id=f"scr-{uuid.uuid4().hex[:8]}",
                name=_GATE_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
            return
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event

    def _build_note(self, cmd: str, err: str) -> str:
        cmd_show = cmd if len(cmd) <= 200 else cmd[:197] + "..."
        err_show = err if len(err) <= 200 else err[:197] + "..."
        return (
            "STOP — you are repeating a command that keeps failing the same way.\n\n"
            f"Repeated command (normalised): `{cmd_show}`\n"
            f"Recurring failure: {err_show}\n\n"
            "Retrying the same command will not change the result. Break the loop:\n"
            "1. Read the error text above literally — it names the exact cause "
            "(missing file/dependency, wrong flag or argument, syntax/type error, "
            "a bound port, a wrong path, or a wrong assumption about the inputs).\n"
            "2. Inspect the relevant inputs first (list/inspect the files, check the "
            "tool's real usage/help, confirm paths and prerequisites) BEFORE running "
            "the command again.\n"
            "3. Change the approach: fix the specific cause, use different "
            "flags/arguments, isolate the smallest failing piece, or reconsider "
            "whether this command is the right way to satisfy the requirement.\n"
            "Do not re-issue the identical command until you have changed something "
            "that addresses this specific error."
        )
