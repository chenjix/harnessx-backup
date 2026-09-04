# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""DeadLoopBreaker — intercept and *block* genuine dead command loops.

Successor to the R1 ``RepeatCommandGuard``. That guard keyed on the command
string alone and was *advisory only* — it appended a break-out nudge to the
tool result but never blocked execution. On this model that turned out to be
insufficient: the agent narrates "I've been stuck in a loop" and then re-runs
the *exact same* command with the *exact same* output, ignoring the advisory,
until it burns the step budget (observed: one task re-ran an identical
read-only diagnostic 12x despite ~10 advisories, ending in agent_error).

Two design changes over the R1 guard, each backed by trajectory evidence:

1. **Key on (command, output-fingerprint), not command alone.**
   A command that is re-run but produces *different* output each time is a
   healthy iterate-and-test loop (e.g. edit-a-file / run-the-build /
   read-the-error). Those passing high-repeat tasks re-ran a ``cat > file``
   write many times but the interleaved build/run output changed, so the
   *consecutive identical* (command, output) count stayed low (<=2). A
   genuine dead loop is the same command returning byte-identical output over
   and over. Keying on the pair cleanly separates the two: it only counts a
   repeat when nothing changed, so productive loops are never touched.

2. **Escalate to a hard block, not just an advisory.**
   Below ``block_threshold`` the guard behaves like the R1 guard (appends an
   escalating advisory to the result). At ``block_threshold`` identical
   (command, output) repeats it stops appending and instead *intercepts* the
   next identical call in ``on_before_tool``: it sets ``approved=False`` and
   returns a synthetic directive, so the dead command never actually runs.
   The agent is forced to change the command or the approach to make any
   progress at all — which is the only thing that ever helps in these loops.

Guarding against false positives:
- Only real Bash commands are tracked (calls with a non-empty ``command``
  field). Malformed empty-argument tool calls (``{}``) — which some passing
  tasks emit many times — carry no command and are ignored entirely.
- The counter resets the moment the output changes, so a single differing
  result immediately un-arms the block for that command.
- ``block_threshold`` is set well above the count any observed *passing* task
  reached on an identical (command, output) pair.

Purely mechanical: below the block threshold it only appends text to the tool
result; at/above it, it uses the framework's documented interception path
(``approved=False`` + ``synthetic_result`` on the ``ToolCallEvent``). It never
mutates message history, so it cannot violate the message contract.
"""

from __future__ import annotations

import dataclasses
import hashlib

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

_REDIRECT = (
    "\n\n[DeadLoopBreaker] You have now run this EXACT command {count} times and "
    "it produced the EXACT same output every time. Repeating it will not change "
    "anything. Do NOT run it again unchanged. Instead either (a) change the "
    "command itself (different flags, a smaller diagnostic step, inspect the "
    "inputs it depends on), or (b) change your approach to the task. State in one "
    "sentence what specifically was wrong with the previous attempts before your "
    "next tool call."
)

_BLOCK = (
    "[DeadLoopBreaker] BLOCKED — this identical command has already been executed "
    "{count} times with byte-identical output, so it has NOT been run again. You "
    "are in a dead loop that is wasting your step budget and will end the task "
    "with a score of 0 if it continues. Running the same command again is "
    "pointless. Take ONE genuinely different action now: (a) if the required "
    "output file(s) can be produced from what you already know, write them to the "
    "exact path named in the task; or (b) run a DIFFERENT, concrete command that "
    "tests a NEW hypothesis about why the previous attempts failed. The exact "
    "command you just tried will remain blocked until you change it."
)


def _normalise(cmd: str) -> str:
    """Collapse whitespace so trivially-reformatted repeats still match."""
    return " ".join(cmd.split())


def _fingerprint(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()


class DeadLoopBreaker(MultiHookProcessor):
    """Detect a genuine (command, identical-output) dead loop and block it."""

    _singleton_group = "tb2_dead_loop_breaker"
    _order = 31  # same slot the R1 guard occupied (after CustomEditToolProcessor)

    def __init__(
        self,
        advisory_threshold: int = 3,
        block_threshold: int = 5,
    ) -> None:
        # advisory_threshold: first advisory fires on the Nth identical
        # (command, output) repeat.
        self.advisory_threshold = max(2, int(advisory_threshold))
        # block_threshold: at/above this many identical (command, output)
        # repeats, the next identical call is intercepted and not executed.
        self.block_threshold = max(self.advisory_threshold + 1, int(block_threshold))
        # cmd_key -> {"fp": last-output-fingerprint, "count": consecutive identical}
        self._state: dict[str, dict] = {}
        # tool_call_id -> cmd_key (set in on_before_tool, consumed in on_after_tool)
        self._pending: dict[str, str] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._state.clear()
        self._pending.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name != "Bash":
            yield event
            return
        cmd = _normalise(str((event.tool_input or {}).get("command", "")))
        if not cmd:
            # Malformed / empty tool call — not a real command; ignore.
            yield event
            return
        st = self._state.get(cmd)
        # If this exact command has already looped with identical output at or
        # past the block threshold, intercept it: do not execute.
        if st is not None and st["count"] >= self.block_threshold:
            yield dataclasses.replace(
                event,
                approved=False,
                synthetic_result=_BLOCK.format(count=st["count"]),
            )
            return
        self._pending[event.tool_call_id] = cmd
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        cmd = self._pending.pop(event.tool_call_id, None)
        if cmd is None:
            yield event
            return
        out_fp = _fingerprint(event.result or "")
        st = self._state.get(cmd)
        if st is not None and st["fp"] == out_fp:
            st["count"] += 1
        else:
            # First run, or output changed -> reset the dead-loop counter.
            st = {"fp": out_fp, "count": 1}
            self._state[cmd] = st

        count = st["count"]
        if count >= self.advisory_threshold and count < self.block_threshold:
            yield dataclasses.replace(
                event, result=(event.result or "") + _REDIRECT.format(count=count)
            )
            return
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._state.clear()
        self._pending.clear()
        yield event
