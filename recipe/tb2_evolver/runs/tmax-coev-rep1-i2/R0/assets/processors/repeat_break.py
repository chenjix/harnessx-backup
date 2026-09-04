# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedActionBreakProcessor — break the "echo trap" degenerate loop.

Closes a systemic small-model failure mode observed on TB2 runs *after* the
R1 length-recovery and R2 loop-detection interventions landed. The remaining
shape is subtle and distinct from both:

* The model issues the **exact same tool call** (same command / same file
  write) turn after turn.
* The in-tree ``LoopDetectionProcessor`` correctly detects this and *appends*
  a warning to the tool-result content ("You are stuck in a loop … try
  something fundamentally different").
* The model **reads the warning, verbally agrees with it** ("The user is
  right — I've been repeating myself", "Let me take a fundamentally different
  approach") … and then emits the *identical* tool call again. The warning,
  buried at the tail of a tool result inside a context now polluted with N
  identical assistant turns, has no purchasing power over the next generation.

Trajectory evidence (R2): tasks reached 9–11 verbatim repeats of a single
``cat > file << EOF`` write or a single query, each preceded by an assistant
turn whose prose acknowledged the loop but whose action was unchanged. The
existing detector's hard-raise threshold sits above these counts, so the run
merely burns budget or terminates with the wrong answer already committed.

The fix is mechanically the same shape as the proven R1 length-recovery
processor, but keyed on a *different* trigger and delivering the intervention
through a *different* channel:

* ``on_after_model`` — fingerprint the assistant turn's tool calls
  (name + canonicalised inputs). Maintain a per-task consecutive-identical
  run counter. A turn whose tool-call fingerprint differs from the previous
  one resets the counter, so legitimate iterative work is never touched.

* ``on_before_model`` — once the same tool call has repeated
  ``repeat_threshold`` times in a row, append **exactly one** ``user``
  message (contract-safe +1 insertion) that:
    - names the concrete repeated command back to the model,
    - forbids re-issuing that same command verbatim,
    - directs it to either change the command materially or, if it believes
      the work is already done, stop and finish.
  The directive escalates on further repeats.

Why a *user turn* rather than the existing tool-result append: an injected
user message lands at the very end of the prompt, is not part of a tool
result the model can gloss over, and (critically) breaks the assistant→tool
→assistant echo cadence that was reproducing the identical generation. This
is the same lever that made the R1 length-recovery nudge effective against
the max_tokens variant of the same underlying degeneracy.

The processor keys purely on structural signals (tool-call name + inputs,
consecutive-run length) and never on task content, so it is
benchmark-agnostic. It does not raise; the in-tree ``LoopDetectionProcessor``
remains the hard-stop safety net. This processor's job is to break the loop
*before* that safety net has to fire, by giving the model a redirect it
cannot bury.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor


def _fingerprint(tool_calls) -> str:
    """Stable fingerprint of an assistant turn's tool calls (name + inputs).

    Empty string when the turn issued no tool calls, so a no-tool-call turn
    (e.g. a pure-narration turn) breaks the run rather than extending it.
    """
    if not tool_calls:
        return ""
    parts = []
    for tc in tool_calls:
        name = getattr(tc, "name", "") or ""
        try:
            inp = json.dumps(getattr(tc, "input", {}) or {}, sort_keys=True, ensure_ascii=False)
        except Exception:
            inp = repr(getattr(tc, "input", {}))
        parts.append(f"{name}\x00{inp}")
    joined = "\x01".join(parts)
    return hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest()[:16]


def _summarise(tool_calls, max_chars: int = 240) -> str:
    """Short human-readable rendering of the repeated command for the nudge."""
    if not tool_calls:
        return "(no command)"
    tc = tool_calls[0]
    name = getattr(tc, "name", "") or "?"
    inp = getattr(tc, "input", {}) or {}
    # Bash tool: the interesting field is `command`; fall back to whole input.
    cmd = ""
    if isinstance(inp, dict):
        cmd = inp.get("command") or inp.get("cmd") or ""
        if not cmd:
            try:
                cmd = json.dumps(inp, ensure_ascii=False)
            except Exception:
                cmd = repr(inp)
    cmd = str(cmd).strip().replace("\n", " ")
    if len(cmd) > max_chars:
        cmd = cmd[:max_chars] + " …"
    return f"`{name}` with: {cmd}"


_NUDGE_FIRST = (
    "You have now issued the SAME command {count} times in a row and gotten the "
    "same result each time:\n\n    {summary}\n\n"
    "Repeating it again will not change the outcome. Do NOT issue that command "
    "again. In your next turn, do exactly ONE of:\n"
    "  (a) run a DIFFERENT command that materially changes your approach "
    "(inspect a different file, test a different hypothesis, or write different "
    "content), or\n"
    "  (b) if you believe the required output already exists and is correct, "
    "stop repeating and finish.\n"
    "Keep your reasoning to at most two sentences before acting."
)

_NUDGE_REPEAT = (
    "STOP. You are stuck in an identical-command loop — you have re-issued the "
    "same command {count} times:\n\n    {summary}\n\n"
    "You keep saying you will try something different and then re-run the exact "
    "same thing. Break the pattern NOW. Your next turn MUST NOT contain that "
    "command. Either (a) attack the problem from a genuinely different angle "
    "with a concrete new command, or (b) verify the required output file exists "
    "at the path the task names and, if it does, finish. One command only, no "
    "long narration."
)


class RepeatedActionBreakProcessor(MultiHookProcessor):
    """Break exact-repeat tool-call loops with an escalating user-turn redirect.

    Parameters
    ----------
    repeat_threshold:
        Number of *consecutive* identical tool-call turns at which the first
        redirect user message is injected. Default 5 — strictly above the
        longest identical run seen in any passing R2 trajectory (the single
        passing task with any repetition topped out at a run of 4; the failing
        loop cluster ran 9–11), so it cannot fire on legitimate iterative work.
    escalate_threshold:
        Consecutive-run length at (or above) which the forceful variant of the
        redirect is used instead of the gentle one. Default 7.
    """

    _singleton_group = "tb2_repeat_break"
    # Run after the length-recovery processor (order=5) in the after-model
    # chain, and well before any hard loop raise. on_before_model ordering is
    # irrelevant here (single +1 insertion).
    _order = 6

    def __init__(
        self,
        repeat_threshold: int = 5,
        escalate_threshold: int = 7,
    ) -> None:
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.escalate_threshold = max(self.repeat_threshold, int(escalate_threshold))
        self._prev_fp: str = ""
        self._run: int = 0
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._prev_fp = ""
        self._run = 0
        self._pending_nudge = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        fp = _fingerprint(event.tool_calls)

        if not fp:
            # No tool call this turn — a pure-narration turn breaks the run.
            self._prev_fp = ""
            self._run = 0
            self._pending_nudge = ""
            yield event
            return

        if fp == self._prev_fp:
            self._run += 1
        else:
            self._prev_fp = fp
            self._run = 1

        if self._run >= self.repeat_threshold:
            template = (
                _NUDGE_REPEAT
                if self._run >= self.escalate_threshold
                else _NUDGE_FIRST
            )
            self._pending_nudge = template.format(
                count=self._run,
                summary=_summarise(event.tool_calls),
            )
        else:
            self._pending_nudge = ""

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=nudge),),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._prev_fp = ""
        self._run = 0
        self._pending_nudge = ""
        yield event
