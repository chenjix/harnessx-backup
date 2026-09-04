# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatToolCallGuard for Tmax (and TB2-style) single-Bash-tool agents.

Closes a systemic failure mode that is distinct from the length-truncation
loop already handled by ``LengthTruncationRecoveryProcessor``.

Observed shape (many ``exit_reason=budget_exceeded`` trajectories):
the model produces a *well-formed, complete* response (``finish_reason`` is
``tool_calls`` / ``stop``, NOT ``length``) whose tool call is **byte-identical**
to a tool call it already issued moments ago. The command returns the same
output, the model re-emits the same narration and the same command, and the
cycle burns the entire step budget without progress. Because the response is
not length-truncated and *does* carry a tool call, none of the existing loop
breakers fire.

This processor watches the stream of emitted tool calls, keyed by a normalised
``(name, input)`` signature. When the same signature recurs ``repeat_threshold``
times inside a sliding window, it injects an escalating corrective ``user``
message before the next generation. The message tells the model that repeating
an identical command yields identical output and instructs it to change
approach — inspect a *different* artifact, alter the command, or move to the
next objective. The offending tool call itself is NOT blocked (the result is
still fed back); we only steer the *next* turn.

This is a mechanical, task-agnostic guard: it keys purely on structural
repetition of the agent's own actions, contains no task-specific literals,
and helps any task class where the model can get stuck re-issuing a command.
"""

from __future__ import annotations

import dataclasses
import json

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor


_NUDGE_FIRST = (
    "You have now issued the SAME command more than once in a row and received "
    "the same output each time. Repeating an identical command cannot produce "
    "new information. Stop re-running it. In your next turn: (a) state in one "
    "sentence what you already learned from that command's output, then (b) take "
    "a DIFFERENT concrete action — inspect a different file, change the command, "
    "run the actual test/build, or move on to the next objective. Do not repeat "
    "the previous command."
)

_NUDGE_REPEAT = (
    "STOP. You are stuck in a loop: you keep issuing the exact same command over "
    "and over, getting the same result, and making no progress. Abandon this line "
    "of investigation completely. Do NOT run that command again under any "
    "phrasing. Re-read the task objectives, pick the single most important "
    "objective that is not yet verified as done, and issue ONE new command that "
    "directly advances it (e.g. write the required output file, restart the "
    "service with the corrected config, or run the verification the task "
    "describes). One new command only."
)


def _sig(name: str, tool_input) -> str:
    """Stable signature for a tool call: name + canonicalised input."""
    try:
        payload = json.dumps(tool_input, sort_keys=True, default=str)
    except Exception:
        payload = repr(tool_input)
    return f"{name}\x00{payload}"


class RepeatToolCallGuard(MultiHookProcessor):
    """Break byte-identical tool-call repetition loops and steer the model.

    Args:
        repeat_threshold: how many times the *same* signature must appear
            within the sliding window before we intervene. ``3`` means the
            third identical call triggers the first nudge.
        window: sliding window (in emitted tool calls) over which repeats are
            counted; older calls age out so unrelated later repeats do not
            accumulate spuriously.
        escalate_threshold: once the same signature has fired the guard this
            many times in the task, escalate to the stronger nudge.
    """

    _singleton_group = "tmax_repeat_tool_call_guard"
    # Run after length_recovery (order 5) so the two loop-breakers do not both
    # try to append in the same chain when their (disjoint) conditions somehow
    # coincide; length truncation takes precedence.
    _order = 6

    def __init__(
        self,
        repeat_threshold: int = 3,
        window: int = 8,
        escalate_threshold: int = 2,
    ) -> None:
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.window = max(self.repeat_threshold, int(window))
        self.escalate_threshold = max(1, int(escalate_threshold))
        self._recent: list[str] = []
        self._fire_counts: dict[str, int] = {}
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._recent = []
        self._fire_counts = {}
        self._pending_nudge = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        # Only well-formed responses that carry at least one tool call are
        # candidates. Length-truncated no-tool-call loops are handled elsewhere.
        if not event.tool_calls:
            yield event
            return

        triggered = False
        for tc in event.tool_calls:
            name = getattr(tc, "name", "") or ""
            tool_input = getattr(tc, "input", None)
            sig = _sig(name, tool_input)
            self._recent.append(sig)
            if len(self._recent) > self.window:
                self._recent = self._recent[-self.window :]
            # Count occurrences of this signature inside the current window.
            occurrences = self._recent.count(sig)
            if occurrences >= self.repeat_threshold:
                triggered = True
                self._fire_counts[sig] = self._fire_counts.get(sig, 0) + 1
                fires = self._fire_counts[sig]
                # Once we have nudged about this signature, reset its window
                # tally so we re-arm only after it recurs again — avoids
                # nudging on every single subsequent step.
                self._recent = [s for s in self._recent if s != sig]

        if triggered:
            escalate = any(c >= self.escalate_threshold for c in self._fire_counts.values())
            self._pending_nudge = _NUDGE_REPEAT if escalate else _NUDGE_FIRST

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Contract-safe insertion: if the last message is already a user turn,
        # replace its content (cannot append after a user); otherwise append a
        # single new user message.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )
