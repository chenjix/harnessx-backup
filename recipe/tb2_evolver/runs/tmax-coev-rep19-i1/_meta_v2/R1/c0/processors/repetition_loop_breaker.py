# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepetitionLoopBreaker — hard-break degenerate self-repeating agent loops.

Closes a systemic failure mode the existing ``LengthTruncationRecovery``
processor does not catch. On this benchmark the model routinely gets stuck
emitting the *same* assistant turn — the same narration and, frequently, the
*byte-identical* Bash command — turn after turn until the step budget is
exhausted (``exit_reason=budget_exceeded`` at the step cap). The run-loop's
passive "please continue" nudge, and even the escalating length-recovery nudge,
are ignored because the model's own verbatim prior turns are still in context
and dominate the next completion. A soft one-line nudge cannot outweigh a wall
of the model's own repeated prose.

Why this is distinct from both existing recovery mechanisms:

* ``LengthTruncationRecovery`` only fires on ``finish_reason == "length"`` with
  no tool call. Many observed loops end normally (``end_turn``) or *carry a
  repeated tool call* every turn — it never fires on those.
* Prior repetition breakers reset their counter on *any* tool call, so a loop
  that re-issues the same failing command each turn evades them entirely. That
  is the dominant shape here: turns that repeat identical narration AND an
  identical, still-failing command dozens of times.

Mechanism (content-based, ``finish_reason``- and tool-call-agnostic):

* Fingerprint every assistant turn from a normalised prefix of its content
  PLUS its first tool call's ``name`` and sorted ``input`` (so "same prose,
  same command" collapses to the same fingerprint whether or not a tool ran).
* When ``repeat_threshold`` consecutive turns share the fingerprint, declare a
  degenerate loop.
* On the next ``before_model``: prune the trailing block of duplicate looping
  assistant turns (and their paired tool-result messages and any passive
  "continue" nudges) so the model is no longer re-primed by its own runaway
  history, then append ONE decisive redirect telling it to abandon the stale
  line and take a single concrete, *different* step.

The intervention is fully content-agnostic: it names no task, path, command, or
constant, so it generalises to any repetition loop the harness encounters.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

_WS_RE = re.compile(r"\s+")

_REDIRECT = (
    "SYSTEM INTERVENTION: your last several turns repeated the same reasoning "
    "and/or the same command almost verbatim without making progress, so that "
    "stale, unproductive history has been removed from the conversation to stop "
    "the loop. Whatever you were retrying is NOT working — do not restate or "
    "re-run it. Step back and choose a genuinely DIFFERENT approach. In this "
    "turn write at most two sentences, then issue exactly ONE Bash tool call "
    "that takes a concrete, different step: inspect the current state to gather "
    "new information, or apply a new fix you have not tried yet. If a previous "
    "command kept failing the same way, change the command."
)


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("content") or ""
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return ""


def _tool_sig(tool_calls) -> str:
    """Stable signature of a turn's tool calls (name + sorted input)."""
    if not tool_calls:
        return ""
    parts = []
    for tc in tool_calls:
        name = getattr(tc, "name", "") or ""
        inp = getattr(tc, "input", None)
        if isinstance(inp, dict):
            try:
                items = sorted((str(k), str(v)) for k, v in inp.items())
            except Exception:
                items = [(str(inp),)]
            inp_str = "|".join(f"{k}={v}" for k, v in items)
        else:
            inp_str = str(inp)
        parts.append(f"{name}({inp_str})")
    return _WS_RE.sub(" ", " ".join(parts)).strip().lower()


def _fingerprint(content, tool_calls, prefix_chars: int) -> str:
    """Normalised prefix of content + tool-call signature."""
    text = _WS_RE.sub(" ", _extract_text(content)).strip().lower()[:prefix_chars]
    return text + " ##TOOLS## " + _tool_sig(tool_calls)


class RepetitionLoopBreaker(MultiHookProcessor):
    """Detect near-identical repeated assistant turns and hard-break the loop."""

    _singleton_group = "tmax_repetition_loop_breaker"
    # Runs just after length-recovery (order 5) so any content collapse it did
    # this turn is already applied, and before compaction (order higher).
    _order = 6

    def __init__(
        self,
        repeat_threshold: int = 3,
        prefix_chars: int = 300,
        min_signal_chars: int = 40,
    ) -> None:
        # Consecutive near-identical turns that trip the breaker. 3 keeps normal
        # short retries (which differ turn-to-turn) safe while catching true
        # verbatim loops quickly — before they exhaust the step budget.
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.prefix_chars = max(80, int(prefix_chars))
        # A turn must carry at least this much signal (content chars OR a tool
        # call) to count toward a loop — avoids tripping on empty/trivial turns.
        self.min_signal_chars = max(0, int(min_signal_chars))
        self._last_fp: str = ""
        self._run: int = 0
        self._pending_break: bool = False

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    def _reset(self) -> None:
        self._last_fp = ""
        self._run = 0
        self._pending_break = False

    async def on_after_model(self, event: ModelResponseEvent):
        text = _extract_text(event.content or "")
        has_signal = len(text.strip()) >= self.min_signal_chars or bool(event.tool_calls)
        if not has_signal:
            # Empty / trivial turn: don't accumulate, don't reset a real streak
            # (rare, but stay conservative and just pass through).
            yield event
            return

        fp = _fingerprint(event.content or "", event.tool_calls, self.prefix_chars)
        if fp and fp == self._last_fp:
            self._run += 1
        else:
            self._run = 1
            self._last_fp = fp

        if self._run >= self.repeat_threshold:
            self._pending_break = True

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_break:
            yield event
            return
        self._pending_break = False
        # After a break we reset the streak so we don't fire again immediately.
        self._last_fp = ""
        self._run = 0

        msgs = list(event.messages)
        if len(msgs) < 3:
            yield self._append_redirect(msgs, event)
            return

        # Recompute the loop fingerprint from the most recent qualifying
        # assistant message.
        target_fp = ""
        for m in reversed(msgs):
            if getattr(m, "role", None) == "assistant":
                cand = _fingerprint(
                    getattr(m, "content", ""),
                    getattr(m, "tool_calls", ()),
                    self.prefix_chars,
                )
                if cand.strip(" #TOOLS"):
                    target_fp = cand
                    break

        if not target_fp:
            yield self._append_redirect(msgs, event)
            return

        # Prune trailing looping assistant turns that match the fingerprint,
        # together with their paired tool-result messages and any passive
        # "continue" nudges. Always keep the first message (task anchor).
        looping_call_ids: set[str] = set()
        for m in msgs:
            if getattr(m, "role", None) == "assistant":
                if _fingerprint(
                    getattr(m, "content", ""),
                    getattr(m, "tool_calls", ()),
                    self.prefix_chars,
                ) == target_fp:
                    for tc in getattr(m, "tool_calls", ()) or ():
                        cid = getattr(tc, "id", None)
                        if cid:
                            looping_call_ids.add(cid)

        pruned: list[Message] = []
        removed = 0
        for idx, m in enumerate(msgs):
            if idx == 0:
                pruned.append(m)
                continue
            role = getattr(m, "role", None)
            if role == "assistant":
                if _fingerprint(
                    getattr(m, "content", ""),
                    getattr(m, "tool_calls", ()),
                    self.prefix_chars,
                ) == target_fp:
                    removed += 1
                    continue
            elif role == "tool":
                # Drop the tool result paired with a pruned looping call.
                cid = getattr(m, "tool_call_id", None)
                if cid and cid in looping_call_ids:
                    removed += 1
                    continue
            elif role == "user":
                utext = _extract_text(getattr(m, "content", "")).strip().lower()
                if (
                    "cut off by the token limit" in utext
                    or "ran into the output token limit" in utext
                    or utext.startswith("system intervention")
                ):
                    removed += 1
                    continue
            pruned.append(m)

        if removed == 0 or not pruned:
            yield self._append_redirect(msgs, event)
            return

        yield self._append_redirect(pruned, event)

    def _append_redirect(self, msgs: list[Message], event: BeforeModelEvent):
        # Contract: never leave a dangling assistant tool_call without its
        # result, and never leave two trailing user messages. If the last
        # message is an assistant turn (its tool result was just pruned, or it
        # was a tool-less turn), append a fresh user redirect. If the last
        # message is already a user turn, replace it to avoid a +1 insertion.
        out = list(msgs)
        if out and getattr(out[-1], "role", None) == "user":
            out[-1] = Message(role="user", content=_REDIRECT)
        else:
            out.append(Message(role="user", content=_REDIRECT))
        return dataclasses.replace(event, messages=tuple(out))

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
