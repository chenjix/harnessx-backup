# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatCommandBlockerProcessor — HARD-break identical-Bash-command loops.

Supersedes the R2 ``RepeatCommandBreakerProcessor`` which injected a redirect
*user* message via ``on_before_model``. That message reached the model only
transiently (the run loop rebuilds the model input from ``state.raw_messages``
each step, and ``on_before_model`` mutations are never written back to state),
so the nudge vanished the next turn and never appeared in persisted history.
Empirically the 9B model narrated but kept re-issuing the same command; the
before_model nudge produced **zero** observable behaviour change across the
whole benchmark round (grep of persisted messages: 0 nudges present).

This processor uses the run loop's supported tool-interception path instead:

* Track the normalized command of each single-Bash-call turn.
* Once the SAME non-empty command has been issued ``block_threshold`` times in
  a row, intercept the offending tool call in ``on_before_tool``: set
  ``approved=False`` and supply a ``synthetic_result``. The run loop then:
    - does NOT execute the (proven-useless) command — saves the round-trip, and
    - writes the synthetic_result into ``state.raw_messages`` as the tool
      result (runloop.py add_raw_message on the synthetic branch), so the
      corrective text PERSISTS in context and compounds turn over turn instead
      of evaporating. The model literally receives the block as the command's
      output, which it cannot silently ignore the way it ignored a floating
      user message.

Safety / Pareto:
* Only fires on a *single* Bash call per turn whose command is non-empty and
  byte-identical to the previous ``block_threshold - 1`` such calls. Empty
  commands (e.g. the self-verify processor's marker turns) are excluded by the
  normalization returning None, so verification loops are untouched.
* ``block_threshold`` defaults to 4 — the agent gets THREE real attempts at the
  exact command before the fourth is blocked. Measured on the incumbent round:
  every passing task's max consecutive non-empty identical run was <= 3; only
  stuck (failing) tasks reached 4+, so the block cannot fire on a currently
  passing trajectory.
* After a block, the counter re-arms: if the model changes the command the
  processor is silent again; if it stubbornly re-issues the identical command
  it is blocked again (escalated wording), never wasting further steps on it.
* Never blocks a *different* command, never kills the run, never touches
  non-Bash turns. No task ids, paths, or command strings are hard-coded.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor


_BLOCK_FIRST = (
    "BLOCKED BY HARNESS — loop guard. You have now issued this EXACT command "
    "{n} times in a row and it produced the same result every time, so it has "
    "NOT been executed again. Running it once more cannot change anything. "
    "Do NOT repeat this command. In your next turn do ONE different thing: "
    "(a) if it failed or produced no useful output, diagnose WHY with a "
    "different command — verify the exact path/file exists, read the real "
    "error, check the tool's actual usage/flags; or (b) if you already have "
    "what you need, move on to the NEXT concrete step of the task. Change "
    "your approach now."
)

_BLOCK_REPEAT = (
    "BLOCKED BY HARNESS — you are STILL re-issuing a command that does not "
    "work; it was not executed. Abandon this line of attack entirely: it will "
    "never succeed as written. Re-read the task, then either fix the "
    "underlying cause (wrong path, missing dependency, wrong tool or flags) "
    "or proceed to a DIFFERENT required step. Your next command must be "
    "genuinely different."
)


def _single_bash_command(tool_calls) -> str | None:
    """Return the normalized command of a single-Bash-call turn, else None."""
    calls = tool_calls or ()
    if len(calls) != 1:
        return None
    call = calls[0]
    if getattr(call, "name", None) != "Bash":
        return None
    inp = getattr(call, "input", None) or {}
    cmd = inp.get("command") if isinstance(inp, dict) else None
    if not isinstance(cmd, str):
        return None
    norm = cmd.strip()
    return norm or None


class RepeatCommandBlockerProcessor(MultiHookProcessor):
    """Hard-break consecutive byte-identical Bash-command loops."""

    _singleton_group = "tmax_repeat_command_blocker"
    _order = 6  # right after LengthTruncationRecoveryProcessor (_order=5)

    def __init__(self, block_threshold: int = 4) -> None:
        # Block the tool call once the same command has been issued this many
        # times in a row (>=2 guards against a nonsensical value).
        self.block_threshold = max(2, int(block_threshold))
        self._last_cmd: str | None = None
        self._run_len: int = 0
        self._blocks_sent: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._last_cmd = None
        self._run_len = 0
        self._blocks_sent = 0
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        # Maintain the consecutive-identical-command run length. This runs
        # BEFORE the tool loop for the same step, so on_before_tool sees the
        # updated run_len for the command about to execute.
        cmd = _single_bash_command(event.tool_calls)
        if cmd is None:
            # Any non-single-Bash turn (multi-call, non-Bash, empty, narration)
            # breaks the run — it represents a change of behaviour.
            self._last_cmd = None
            self._run_len = 0
            yield event
            return

        if cmd == self._last_cmd:
            self._run_len += 1
        else:
            self._last_cmd = cmd
            self._run_len = 1
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Only consider Bash tool calls that match the tracked looping command.
        if event.tool_name != "Bash":
            yield event
            return
        inp = event.tool_input or {}
        cmd = inp.get("command") if isinstance(inp, dict) else None
        norm = cmd.strip() if isinstance(cmd, str) else None
        if not norm or norm != self._last_cmd:
            yield event
            return

        if self._run_len < self.block_threshold:
            yield event
            return

        # Loop confirmed: block this execution and inject a persistent result.
        self._blocks_sent += 1
        msg = (
            _BLOCK_REPEAT
            if self._blocks_sent >= 2
            else _BLOCK_FIRST.format(n=self._run_len)
        )
        # Re-arm so a genuinely different next command is untouched, but a
        # stubborn re-issue of the identical command is blocked again.
        self._run_len = 0
        self._last_cmd = None
        yield dataclasses.replace(
            event,
            approved=False,
            synthetic_result=msg,
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._last_cmd = None
        self._run_len = 0
        self._blocks_sent = 0
        yield event
