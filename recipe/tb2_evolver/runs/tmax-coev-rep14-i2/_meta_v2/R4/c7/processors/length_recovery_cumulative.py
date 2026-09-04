# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""CumulativeLengthTruncationRecovery for Tmax / TB2-style agents.

Supersedes the stock ``LengthTruncationRecoveryProcessor``. It closes the same
systemic failure — the model hits ``max_tokens`` (``finish_reason == "length"``)
without emitting a tool call, the run loop appends a passive "please continue"
nudge, and the model re-primes the same runaway narration — but fixes a blind
spot that let the loop persist for a whole budget:

* The stock processor escalates only on **consecutive** truncations. The
  pathological shape observed in the wild is *alternating*
  truncate -> nudge -> one-command -> truncate: the model burns a full 4096-token
  turn re-narrating "I'm stuck" before every single command. A tool call resets
  the consecutive counter, so the hard escalation never fires and the collapsed
  narration (head+tail ~2 kB) keeps piling up in context, re-priming the loop
  turn after turn. The task drains its whole step budget without ever recovering.

This processor tracks **cumulative** length-truncations per task, not just the
consecutive run:

* On any length truncation it collapses the runaway assistant content (head+tail)
  exactly as before and replaces the passive continue nudge with a corrective
  "issue ONE concrete command" instruction.
* Once cumulative truncations in the task cross ``chronic_threshold`` — i.e. the
  agent is in a chronic re-narration loop, not a one-off overrun — it (a)
  escalates to a terminal "STOP narrating, one minimal command only" directive
  and (b) collapses the offending assistant turn to a short stub instead of
  head+tail, so the accumulated "I'm stuck" narration stops dominating context
  and re-priming the loop.

It never force-exits and never removes messages, so it cannot regress a task
that truncates a few times early and then recovers on its own (that agent's
productive turns simply proceed; the only effect on it is a slightly shorter
collapsed message on the late truncations, which is harmless). All mutations are
contract-safe: ``on_after_model`` rewrites only the model event's own content and
``on_before_model`` only rewrites the trailing user message (never adds/removes).
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

_HEAD_CHARS = 1200
_TAIL_CHARS = 600

_TRUNC_MARKER = (
    "\n\n[response truncated by harness: the model hit the output token limit "
    "without issuing a tool call; the omitted middle was discarded to prevent a "
    "repetition loop]\n\n"
)

# Replaces the whole runaway turn once the agent is in a chronic loop: keeping
# only a short stub stops the repeated narration from re-priming the loop.
_STUB = (
    "[response discarded by harness: the model hit the output token limit with no "
    "tool call for the Nth time in this task — the repeated narration was dropped "
    "to break the loop]"
)

_NUDGE_FIRST = (
    "Your last turn ran into the output token limit without running any command. "
    "Do NOT re-explain or continue the previous narration. In your next turn write "
    "at most two sentences of reasoning, then issue exactly ONE concrete Bash "
    "command that makes progress (inspect a file, run a script, or write output). "
    "Keep the response short."
)

_NUDGE_REPEAT = (
    "STOP. You have now hit the output token limit multiple turns in a row, which "
    "means you are repeating yourself instead of acting. Abandon the current line "
    "of narration entirely. Do not write any prose analysis. Respond with a SINGLE "
    "short Bash tool call that either (a) writes the required output file(s) to the "
    "path named in the task, or (b) runs a quick command to check the current state "
    "so you can decide the next concrete step. One command only."
)

_NUDGE_CHRONIC = (
    "STOP NARRATING. You have now hit the output token limit many times in this "
    "task while making no durable progress — you are stuck in a re-narration loop "
    "and burning your budget. Ignore everything you were just saying. Write NO "
    "analysis and NO explanation. Your entire next response must be exactly ONE "
    "short Bash tool call. Prefer a command that (a) makes ONE small, different, "
    "concrete change from what you have already tried (if a command failed twice, "
    "do something else), or (b) creates or corrects the REQUIRED OUTPUT FILE at the "
    "exact path named in the task (re-read the task for the exact path — a file at "
    "the wrong path scores zero). One command, nothing else."
)


class CumulativeLengthTruncationRecovery(MultiHookProcessor):
    """Break the max_tokens repetition loop using cumulative-truncation escalation."""

    # Occupy the same singleton group / order slot as the stock processor so this
    # cleanly replaces it in the pipeline.
    _singleton_group = "tmax_length_recovery"
    _order = 5

    def __init__(
        self,
        repeat_threshold: int = 2,
        chronic_threshold: int = 4,
        head_chars: int = _HEAD_CHARS,
        tail_chars: int = _TAIL_CHARS,
    ) -> None:
        self.repeat_threshold = max(1, int(repeat_threshold))
        # chronic escalation should never fire before the consecutive escalation
        self.chronic_threshold = max(self.repeat_threshold + 1, int(chronic_threshold))
        self.head_chars = max(0, int(head_chars))
        self.tail_chars = max(0, int(tail_chars))
        self._consecutive: int = 0
        self._cumulative: int = 0
        self._pending_nudge: str = ""

    def _reset(self) -> None:
        self._consecutive = 0
        self._cumulative = 0
        self._pending_nudge = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        length_truncated = event.finish_reason == "length" and not event.tool_calls
        if not length_truncated:
            # A productive turn (or any non-length stop) resets the *consecutive*
            # run but NOT the cumulative count — chronic loops that alternate
            # truncate/act must still escalate.
            self._consecutive = 0
            self._pending_nudge = ""
            yield event
            return

        self._consecutive += 1
        self._cumulative += 1

        chronic = self._cumulative >= self.chronic_threshold
        if chronic:
            self._pending_nudge = _NUDGE_CHRONIC
        elif self._consecutive >= self.repeat_threshold:
            self._pending_nudge = _NUDGE_REPEAT
        else:
            self._pending_nudge = _NUDGE_FIRST

        content = event.content or ""
        if chronic:
            # Chronic loop: drop the runaway narration entirely so it stops
            # re-priming the loop; keep only a short stub.
            if len(content) > len(_STUB):
                yield dataclasses.replace(event, content=_STUB)
            else:
                yield event
            return

        if len(content) > (self.head_chars + self.tail_chars + len(_TRUNC_MARKER)):
            collapsed = (
                content[: self.head_chars]
                + _TRUNC_MARKER
                + (content[-self.tail_chars :] if self.tail_chars else "")
            )
            yield dataclasses.replace(event, content=collapsed)
        else:
            yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # The run loop appends a passive "continue" user message for
        # finish_reason=length. Replace that trailing user message rather than
        # inserting (keeps the before_model net length change at 0).
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
