# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LengthTruncationRecoveryProcessor (v4 — collapse the re-seeding spiral).

Closes the systemic ``budget_exceeded`` / step-cap failure mode that still
dominates the small-model TB2 runs after v3: on 7 of the round's failing tasks
the model emits 10-20 length-truncated turns (``finish_reason == "length"``,
``output_tokens == 4096``, no tool call), grinds 65-92 messages, and burns the
step budget. v3 (repointed in R1) correctly *rewrote* the run loop's passive
"continue from where you left off" nudge into an actionable "run ONE command"
directive — and the trajectories confirm the model **reads and acknowledges**
it ("The user is telling me to stop the repetitive analysis and just run a
single Bash command...") — but then keeps spiralling.

Root cause refined from R2 trajectory bodies
---------------------------------------------
The truncated turns are **not** degenerate token-repetition. They are genuine,
verbose chain-of-thought ("Wait, I think I see the issue now! Let me trace
through the code more carefully: 1. ... 2. ...") that runs *past* the 4096
output-token cap before the model ever reaches a tool call. Two mechanical
amplifiers keep the loop alive across turns:

1. **Re-seeding tail.** v3 collapsed an oversized turn to ``head(1200) +
   marker + tail(600)``, but the preserved *tail* is exactly the mid-reasoning
   continuation the next turn latches onto and keeps developing. The kept tail
   re-seeds the spiral.
2. **Barely-triggered collapse.** Most truncated turns sit right at v3's
   ``head+tail+marker`` collapse threshold, so v3 frequently left the *entire*
   runaway turn in history. Across a long run many full-length reasoning turns
   accumulate in context (input tokens grow steadily turn over turn), which
   both costs budget and keeps re-priming the same unfinished thought.

v4 fix (Configuration + Control, same lever family as v3, new evidence)
-----------------------------------------------------------------------
* ``on_after_model``: collapse every truncated no-tool-call turn down to a
  **short head stub with NO tail** (defaults head=400, tail=0). The middle and
  the re-seeding continuation tail are discarded and replaced by a terminal
  marker that itself says the reasoning was abandoned. The model can no longer
  re-read and continue its own unfinished spiral, and accumulated context stays
  small. The collapse threshold drops to ``head+tail+len(marker)`` (~600), so
  it now fires on *every* ~2000-char truncated turn, not just the largest.
* ``on_before_model``: unchanged contract-safe behaviour from v3 — rewrite the
  last user message (the passive nudge) in place (len_delta 0) into an
  escalating actionable directive, or append one user message when the last
  role is not ``user`` (+1). The escalated directive now tells the model to
  emit its command via a single heredoc write with essentially no prose, giving
  it a concrete short-output path instead of asking it to self-limit reasoning
  it cannot self-limit.

The processor keys purely on ``finish_reason`` / ``tool_calls`` / ``content``
length and on the harness's own passive-nudge text — never on task content, so
it is benchmark-agnostic and carries no task-specific literals.
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


_HEAD_CHARS = 400
_TAIL_CHARS = 0
_CONTENT_CHAR_THRESHOLD = 40000

# Substring of the run loop's passive continuation message. If the last user
# message contains this, it is the re-priming nudge we want to overwrite.
_PASSIVE_MARKER = "cut off by the token limit"

# Replaces the discarded middle + tail of a runaway truncated turn. Terminal by
# design: it tells the model the abandoned reasoning is gone so it does not try
# to reconstruct it.
_TRUNC_MARKER = (
    "\n\n[the rest of this turn was a long reasoning spiral that hit the token "
    "limit with no command; it has been discarded. Do NOT reconstruct or "
    "continue it — start the next turn with a command.]"
)

# First occurrence: gentle but concrete redirect.
_NUDGE_FIRST = (
    "Your last turn was cut off by the token limit before you ran any command. "
    "Do NOT continue or re-explain the previous narration — that is what caused "
    "the cut-off. In your next turn write at most TWO short sentences of "
    "reasoning, then issue exactly ONE concrete Bash command that makes forward "
    "progress (inspect a file, run/compile a script, or write an output file). "
    "Keep the whole response short so it is not truncated again."
)

# Repeated occurrence: hard reset — the model keeps spending its whole output
# budget on reasoning instead of acting. Give it a concrete short-output path
# (a single heredoc write) rather than asking it to shorten reasoning it cannot
# shorten on its own.
_NUDGE_REPEAT = (
    "STOP. Your last few turns were all cut off mid-reasoning because you spend "
    "the entire response thinking instead of acting. Write NO analysis this "
    "turn. Emit exactly ONE Bash tool call and nothing else. If you were about "
    "to write a file, do it now with a single heredoc, e.g. "
    "`cat > /path/to/target <<'EOF' ... EOF`; otherwise run ONE short command to "
    "inspect the current state (ls, cat, or run the failing script) so the next "
    "step has fresh output to act on. One tool call. Zero prose."
)


class LengthTruncationRecoveryProcessor(MultiHookProcessor):
    """Break the max_tokens reasoning-spiral loop and redirect the model to act.

    Parameters
    ----------
    repeat_threshold:
        Number of *consecutive* truncations at (or above) which the escalated
        nudge is used instead of the gentle one. Default 2.
    head_chars / tail_chars:
        How much of a runaway content blob to keep when collapsing it. v4
        default keeps a small head and NO tail so the re-seeding mid-reasoning
        continuation is discarded.
    content_char_threshold:
        Secondary trigger: assistant ``content`` length (chars) at or above
        which a no-tool-call turn is treated as a runaway truncation even when
        ``finish_reason`` is not reported as ``"length"``. The primary trigger
        is ``finish_reason == "length"``, which is reliable on this backend.
    """

    _singleton_group = "tb2_length_recovery"
    _order = 5

    def __init__(
        self,
        repeat_threshold: int = 2,
        head_chars: int = _HEAD_CHARS,
        tail_chars: int = _TAIL_CHARS,
        content_char_threshold: int = _CONTENT_CHAR_THRESHOLD,
    ) -> None:
        self.repeat_threshold = max(1, int(repeat_threshold))
        self.head_chars = max(0, int(head_chars))
        self.tail_chars = max(0, int(tail_chars))
        self.content_char_threshold = max(1, int(content_char_threshold))
        self._consecutive: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._consecutive = 0
        self._pending_nudge = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        content = event.content or ""
        no_tool_call = not event.tool_calls
        # Primary trigger: explicit length finish (reliable on this backend).
        # Secondary: an oversized no-tool-call content blob (other backends).
        length_truncated = no_tool_call and (
            event.finish_reason == "length"
            or len(content) >= self.content_char_threshold
        )
        if not length_truncated:
            # Any normal / tool-calling turn clears the streak.
            self._consecutive = 0
            self._pending_nudge = ""
            yield event
            return

        self._consecutive += 1
        self._pending_nudge = (
            _NUDGE_REPEAT
            if self._consecutive >= self.repeat_threshold
            else _NUDGE_FIRST
        )

        # Collapse the runaway turn to a short head + terminal marker (no tail).
        # This discards the re-seeding mid-reasoning continuation and keeps the
        # accumulated context small across a truncation streak.
        keep_budget = self.head_chars + self.tail_chars + len(_TRUNC_MARKER)
        if len(content) > keep_budget:
            collapsed = content[: self.head_chars] + _TRUNC_MARKER
            if self.tail_chars:
                collapsed = collapsed + content[-self.tail_chars :]
            yield dataclasses.replace(event, content=collapsed)
        else:
            yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""

        messages = event.messages
        if messages and messages[-1].role == "user":
            # Contract-safe path: rewrite ONLY the last user message's content
            # (len_delta == 0). This overwrites the run loop's passive "continue
            # from where you left off" re-priming nudge with an actionable
            # directive; history is otherwise untouched.
            last = messages[-1]
            existing = last.content or ""
            if _PASSIVE_MARKER in existing:
                new_content = nudge
            else:
                new_content = nudge + "\n\n" + existing
            new_last = dataclasses.replace(last, content=new_content)
            yield dataclasses.replace(
                event, messages=messages[:-1] + (new_last,)
            )
        else:
            # Last role is not user (e.g. a tool result) — append exactly one
            # user message (+1, contract legal).
            yield dataclasses.replace(
                event,
                messages=messages + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._consecutive = 0
        self._pending_nudge = ""
        yield event
