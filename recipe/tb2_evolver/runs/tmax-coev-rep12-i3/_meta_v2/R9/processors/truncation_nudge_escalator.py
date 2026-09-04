# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""TruncationNudgeEscalator — durably defuse the *interleaved* max_tokens loop.

Closes a systemic failure the two existing loop-breakers both MISS.

Two loop-breakers already live in the pipeline:

* ``LengthTruncationRecoveryProcessor`` (order 5) rewrites the passive
  "cut off by the token limit … please continue" nudge into a firm terseness
  directive in ``on_before_model`` — but that edit is EPHEMERAL. The run loop
  re-assembles each step's input from ``state.raw_messages`` (which still holds
  the passive nudge), so the firm directive the model sees for one turn
  evaporates and the passive "please continue" wall keeps growing in the
  persisted history.
* ``TruncationLoopCompactor`` (order 20) collapses a run of ``>= min_run``
  *consecutive* near-identical truncated assistant turns that carry NO tool
  call. It explicitly does not count a truncated turn that carries a (partial)
  tool call, so a single interleaved tool-call turn breaks the "consecutive"
  run and the compactor never fires.

Failure shape this processor targets (measured on the R8 draw, all
``budget_exceeded`` at step 80, all reward 0):

    assistant  ~4096-token prose + partial tool call (finish_reason=length)
    user       "Your previous response was cut off by the token limit …"
    tool       <result of the partial call>
    assistant  ~4096-token prose (no tool call) (finish_reason=length)
    user       "Your previous response was cut off by the token limit …"
    ... the SAME passive nudge accrues 8-23 times, interleaved with tool
        calls and PostCompaction messages, until budget_exceeded ...

Because the truncated turns are separated by tool results, they are never
``min_run`` consecutive, so ``TruncationLoopCompactor`` never collapses them;
and because the firm nudge never persists, the model keeps staring at a wall
of passive "please continue" messages and its own repeated narration prefix,
which re-primes the runaway generation every step.

Fix (durable, structural, no literals):

Runs at ``on_step_start`` (like ``TruncationLoopCompactor``), so any edit to
``event.messages`` changes the history hash and the run loop auto-generates a
SegmentBoundary that writes the trimmed window durably into
``state.raw_messages`` / ``state.messages`` (runloop.py ~345-368) — the same
durable path the other on_step_start compactors use.

When the assembled window contains ``>= nudge_threshold`` passive
"cut off by the token limit" nudges (a count that is provably absent from the
passing set — see the round journal), it:

* drops every passive nudge EXCEPT the most recent, replacing that one with a
  single firm terseness directive (kills the accreted "please continue" wall
  that primes the loop and instructs the model to be terse);
* head+tail-trims every over-long truncated assistant narration turn
  (``>= trim_over_chars``), whether or not it carries a tool call — the
  interleaved-tool-call turns are exactly what the existing compactor cannot
  touch. Tool calls themselves are preserved verbatim; only the runaway prose
  ``content`` is trimmed.

It never blocks, drops, or fabricates a tool call, never touches ``tool``
messages, and never removes the leading (task-description) message. The
trigger is a structural repetition count, so it generalizes to any task that
falls into the interleaved truncation loop.
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    Message,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Substring identifying the run loop's default passive length-truncation nudge
# (harnessx/core/runloop.py). Matched loosely so minor wording drift still
# classifies the message as a passive continue nudge.
_PASSIVE_NUDGE_MARKER = "cut off by the token limit"

_FIRM_DIRECTIVE = (
    "[harness: your responses have repeatedly hit the output token limit while "
    "writing long reasoning, and the redundant 'please continue' prompts have "
    "been cleaned out of this context. This is a hard cap you cannot raise by "
    "writing more. STOP producing long analysis. Your next turn must be SHORT: "
    "at most two sentences, then exactly ONE concrete Bash command that makes "
    "real progress (inspect a specific file, run a script, or write the "
    "required output to the path the task names). Do not restate the plan or "
    "re-describe what you already know.]"
)

_TRUNC_TRIM_MARKER = (
    "\n\n[harness: this over-long turn hit the output token limit; the middle "
    "was discarded to stop a repetition loop. Be terse and act.]\n\n"
)


class TruncationNudgeEscalator(MultiHookProcessor):
    """Durably defuse the interleaved max_tokens truncation loop."""

    _singleton_group = "tmax_truncation_nudge_escalator"
    # After compaction (order 8) and the consecutive-no-tool TruncationLoopCompactor
    # (order 20) / IdenticalCommandLoopCompactor (order 21). Runs last so it
    # cleans up whatever those two left behind.
    _order = 22

    def __init__(
        self,
        nudge_threshold: int = 8,
        head_chars: int = 1200,
        tail_chars: int = 400,
        trim_over_chars: int = 2400,
    ) -> None:
        # Minimum number of passive "cut off by the token limit" nudges that
        # must be present in the assembled window before we fire. Set safely
        # above the passing set's max (see journal: highest passing nudge count
        # is 6) so it cannot fire on a currently-passing trajectory.
        self.nudge_threshold = max(3, int(nudge_threshold))
        self.head_chars = max(200, int(head_chars))
        self.tail_chars = max(0, int(tail_chars))
        # Only trim truncated-narration turns longer than this; leaves ordinary
        # substantive turns untouched.
        self.trim_over_chars = max(
            self.head_chars + self.tail_chars + 200, int(trim_over_chars)
        )

    async def on_task_start(self, event: TaskStartEvent):
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        yield event

    def _is_passive_nudge(self, m: Message) -> bool:
        return (
            getattr(m, "role", None) == "user"
            and isinstance(getattr(m, "content", ""), str)
            and _PASSIVE_NUDGE_MARKER in (getattr(m, "content", "") or "")
        )

    def _trim_content(self, content: str) -> str:
        if not isinstance(content, str):
            return content
        if len(content) < self.trim_over_chars:
            return content
        tail = content[-self.tail_chars :] if self.tail_chars else ""
        return content[: self.head_chars] + _TRUNC_TRIM_MARKER + tail

    async def on_step_start(self, event: StepStartEvent):
        msgs = list(event.messages)
        if not msgs:
            yield event
            return

        # Count passive nudges in the assembled window.
        nudge_idxs = [i for i, m in enumerate(msgs) if self._is_passive_nudge(m)]
        if len(nudge_idxs) < self.nudge_threshold:
            yield event
            return

        last_nudge_idx = nudge_idxs[-1]
        drop = set(nudge_idxs[:-1])  # drop all passive nudges except the most recent

        out: list[Message] = []
        changed = False
        for i, m in enumerate(msgs):
            if i == 0:
                # Never touch the leading (task-description) message.
                out.append(m)
                continue
            if i in drop:
                changed = True
                continue  # remove this redundant passive nudge
            if i == last_nudge_idx:
                # Replace the surviving passive nudge with the firm directive.
                out.append(Message(role="user", content=_FIRM_DIRECTIVE))
                changed = True
                continue
            # Trim over-long assistant narration (with or without a tool call);
            # tool calls / tool-call metadata are preserved, only prose shrinks.
            if getattr(m, "role", None) == "assistant":
                content = getattr(m, "content", "") or ""
                trimmed = self._trim_content(content)
                if trimmed is not content and trimmed != content:
                    out.append(dataclasses.replace(m, content=trimmed))
                    changed = True
                    continue
            out.append(m)

        if not changed or not out:
            yield event
            return

        # Safety: preserve the leading message identity.
        if out[0] is not msgs[0]:
            out = [msgs[0]] + [x for x in out if x is not msgs[0]]

        yield dataclasses.replace(event, messages=tuple(out))
