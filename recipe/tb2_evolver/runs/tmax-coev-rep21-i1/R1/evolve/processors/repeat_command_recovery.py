# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandRecoveryProcessor for Tmax (and TB2-style) agents.

Closes a systemic failure mode that is orthogonal to the max_tokens loop
handled by ``LengthTruncationRecoveryProcessor``: the agent gets *stuck
re-issuing the exact same tool call and receiving the exact same result*,
turn after turn, without changing its approach.

Observed shape (r0 trajectories): on 15/25 failing tasks the agent emitted
a run of >=4 (frequently 15-33) consecutive identical Bash calls that each
returned an identical result — usually an identical error traceback. The
model narrates "let me try a different approach" but then re-issues the
byte-for-byte same command. Nothing in the pipeline detects this: the parse
retry guard only fires on parse errors, and the length-recovery guard only
fires on ``finish_reason == "length"``. So the agent burns its entire step
budget hammering a command that provably cannot make progress.

This processor:
* fingerprints each (tool_name, tool_input, result) triple as it completes
* counts consecutive repeats of the identical fingerprint
* once the run reaches ``repeat_threshold``, injects a corrective user
  message before the next model call that (a) states the repetition was
  detected, (b) states that re-running the same command will keep producing
  the same output, and (c) instructs the agent to change its approach
  (inspect why the command fails / try a materially different command /
  stop and report) rather than re-issue it.

The nudge is a generic strategy prompt: it names no task, path, command, or
constant — it applies to any stuck-repetition loop on any task.
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

_NUDGE_FIRST = (
    "LOOP DETECTED: your last {n} tool calls were byte-for-byte identical and "
    "each returned the identical result. Re-running the same command will keep "
    "producing the same output — you are not making progress. Do NOT issue that "
    "command again. Instead, in your next turn: (1) read the previous result "
    "carefully to understand WHY it is not what you want, then (2) issue a "
    "MATERIALLY DIFFERENT command that addresses the actual cause (e.g. inspect "
    "the environment, fix a prerequisite, or take a different route to the goal). "
    "One concrete, different command."
)

_NUDGE_REPEAT = (
    "STILL LOOPING: you have now repeated the same failing command many times. "
    "Stop retrying it entirely — it cannot succeed as written. Step back and "
    "diagnose the root cause from the error you keep seeing, then take a "
    "different concrete action. If the error names a conflict, name collision, "
    "missing dependency, wrong path, or wrong working directory, address THAT "
    "first with a single different command. Do not narrate at length; act "
    "differently."
)


class RepeatedCommandRecoveryProcessor(MultiHookProcessor):
    """Break identical-tool-call/identical-result repetition loops."""

    _singleton_group = "tmax_repeat_command_recovery"
    # After length recovery (5) / time reminder (6); before compaction (8).
    _order = 7

    def __init__(
        self,
        repeat_threshold: int = 3,
        result_fingerprint_chars: int = 2000,
    ) -> None:
        # repeat_threshold counts the number of *consecutive identical*
        # (call, result) pairs required before the guard fires. threshold=3
        # means: the 1st call, an identical 2nd, an identical 3rd -> fire
        # before the model would produce a 4th.
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.result_fingerprint_chars = max(0, int(result_fingerprint_chars))
        self._last_fp: str | None = None
        self._run_len: int = 0
        self._pending_nudge: str = ""

    def _reset(self) -> None:
        self._last_fp = None
        self._run_len = 0
        self._pending_nudge = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        # Build a fingerprint from the tool identity + the result text.
        # tool_input is not on ToolResultEvent, but for the stuck-loop shape
        # an identical result from the same tool is a reliable signal; we
        # additionally key on tool_call_id-independent content.
        result_text = event.result or ""
        err_text = event.error or ""
        fp = "\x00".join(
            (
                event.tool_name or "",
                result_text[: self.result_fingerprint_chars],
                err_text[: self.result_fingerprint_chars],
            )
        )

        if fp == self._last_fp:
            self._run_len += 1
        else:
            self._last_fp = fp
            self._run_len = 1

        if self._run_len >= self.repeat_threshold:
            # Escalate the nudge the deeper the loop goes.
            if self._run_len >= self.repeat_threshold + 2:
                self._pending_nudge = _NUDGE_REPEAT
            else:
                self._pending_nudge = _NUDGE_FIRST.format(n=self._run_len)

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Contract: do not create two consecutive user messages. If the run
        # loop already left a trailing user message, append our nudge onto it
        # rather than inserting a new one.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            prev = msgs[-1].content or ""
            merged = (prev + "\n\n" + nudge) if prev else nudge
            msgs[-1] = Message(role="user", content=merged)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
