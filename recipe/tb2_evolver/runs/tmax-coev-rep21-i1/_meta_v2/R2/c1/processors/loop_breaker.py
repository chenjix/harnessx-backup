# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LoopBreakerProcessor — hard-stop for deep identical-command/identical-result
loops that soft text nudges fail to break on a weak model.

Motivation (observed on r0 tmax trajectories):
    A small model (Qwen3.5-4B) frequently gets *stuck* re-issuing the same
    (or near-identical) shell command that returns the same error, turn after
    turn — 10 to 34 consecutive repeats on failing tasks (e.g. a `tesseract`
    call with a broken flag repeated ~13x, a `cat > main.rs << EOF` heredoc
    repeated ~33x, a `ps aux | grep` repeated ~26x). The pipeline already has
    two *soft* guards:

      * ``LengthTruncationRecoveryProcessor`` — injects a text nudge on
        ``finish_reason == "length"``.
      * ``RepeatedCommandRecoveryProcessor`` — injects a text nudge after N
        *strictly consecutive* identical (tool, result) pairs.

    Both fire, but on this model the injected *user-message* nudge is ignored:
    the model reads "please try something different" and then re-issues the
    byte-for-byte same command. Two mechanical gaps let the loop persist:

      1. **Interleaving resets the consecutive counter.** A ``finish_reason ==
         "length"`` turn produces an assistant message with *no* tool result,
         so the strictly-consecutive counter in the existing guard resets even
         though the *same* failing command resumes right after.
      2. **A text nudge has no teeth.** The model still gets the tool executed
         and its expected (failing) output, so nothing forces a change.

This processor closes both gaps without regressing legitimate retries:

    * It fingerprints (tool_name, normalised_command, normalised_result) and
      counts recurrences within a *rolling window* — tolerant of interleaved
      turns, so a length-truncation in the middle does not reset the count.
    * Normalisation strips volatile tokens (digits, timestamps, hex ids,
      whitespace) so cosmetically-varying-but-semantically-identical failures
      (e.g. a stderr tail that differs by a PID) still cluster.
    * Once a signature has produced the *same* result ``block_threshold`` times,
      the NEXT attempt at that exact signature is **blocked** (``approved=False``)
      and replaced with a synthetic result that (a) states the command was
      refused because it produced the identical output N times, (b) echoes a
      short slice of the recurring output, and (c) demands a materially
      different command — or, if no progress is possible, writing the required
      deliverable / reporting status.

Design choices for Pareto safety:
    * ``block_threshold`` defaults to 6 — far past where a legitimate poll
      ("wait for service to come up") would have changed its output. A
      polling loop whose output legitimately *changes* never accumulates a
      repeat count, so it is never blocked.
    * Only the *specific* offending signature is blocked. A different command
      (even one char different after normalisation) passes through untouched,
      so the model can always make forward progress.
    * The block is a one-shot per signature-escalation: after blocking once we
      keep blocking that exact signature until the model changes it, but we
      never touch anything else.
    * It names no task, path, command, or constant — it is a generic
      loop-breaker that applies to any stuck-repetition loop on any task.

This is a Control-lever mechanism (mechanical hook around the run loop), not an
Instruction change: the earlier text-nudge instructions already exist and were
demonstrably insufficient; the missing piece is a mechanical refusal.
"""

from __future__ import annotations

import dataclasses
import re
from collections import deque

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Volatile-token scrubbers so semantically-identical failures cluster even when
# a PID / timestamp / address / whitespace differs between repeats.
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")


def _normalise(text: str, max_chars: int) -> str:
    if not text:
        return ""
    t = text[:max_chars]
    t = _HEX.sub("0xN", t)
    t = _NUM.sub("N", t)
    t = _WS.sub(" ", t)
    return t.strip()


_BLOCK_MESSAGE = (
    "[LoopBreaker] REFUSED TO EXECUTE. You have now issued this same command and "
    "received the same result {n} times. Re-running it is provably not making "
    "progress, so the harness did NOT execute it this time. The recurring result "
    "was:\n"
    "---\n{sample}\n---\n"
    "You must change approach NOW. Do exactly one of the following in your next "
    "turn, as a SINGLE concrete command:\n"
    "  1. Diagnose the root cause named in the result above (wrong flag/syntax, "
    "missing dependency, wrong path, wrong working directory) and issue a "
    "MATERIALLY DIFFERENT command that fixes it; or\n"
    "  2. If you cannot extract what you need this way, take a different route to "
    "the goal (a different tool, a simpler invocation, or read the input another "
    "way); or\n"
    "  3. If you already have enough to proceed, stop retrying and WRITE the "
    "required output file(s) to the exact path(s) named in the task.\n"
    "Do not repeat the refused command. Keep your reasoning to at most two "
    "sentences, then act."
)


class LoopBreakerProcessor(MultiHookProcessor):
    """Mechanically block a command that keeps returning the identical result."""

    # Run before tools execute (so we can refuse), and after the earlier soft
    # nudges (length=5, repeat=7) have already had their chance. bg_install_guard
    # is 15; keep this just after so ordinary guards run first.
    _singleton_group = "tmax_loop_breaker"
    _order = 16

    def __init__(
        self,
        block_threshold: int = 6,
        window: int = 24,
        command_fingerprint_chars: int = 400,
        result_fingerprint_chars: int = 600,
        sample_chars: int = 400,
    ) -> None:
        # block_threshold counts how many times the SAME (command, result)
        # signature must have already occurred (within the rolling window)
        # before the NEXT attempt at it is refused. Kept high so legitimate
        # short retries and polling loops (whose output changes) are untouched.
        self.block_threshold = max(3, int(block_threshold))
        self.window = max(self.block_threshold + 2, int(window))
        self.command_fingerprint_chars = max(0, int(command_fingerprint_chars))
        self.result_fingerprint_chars = max(0, int(result_fingerprint_chars))
        self.sample_chars = max(0, int(sample_chars))
        # tool_call_id -> normalised command signature (captured before_tool,
        # read after_tool since ToolResultEvent has no tool_input).
        self._pending_cmd: dict[str, str] = {}
        # rolling window of recent (command_sig, result_sig) fingerprints.
        self._recent: deque[str] = deque(maxlen=self.window)
        # last observed short sample per fingerprint, for the block message.
        self._sample: dict[str, str] = {}

    def _reset(self) -> None:
        self._pending_cmd.clear()
        self._recent.clear()
        self._sample.clear()

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event

    def _command_sig(self, event: ToolCallEvent) -> str:
        command = ""
        if isinstance(event.tool_input, dict):
            command = str(event.tool_input.get("command", "") or "")
        else:
            command = str(event.tool_input or "")
        return _normalise(command, self.command_fingerprint_chars)

    async def on_before_tool(self, event: ToolCallEvent):
        cmd_sig = self._command_sig(event)
        # Record the command signature so on_after_tool can pair it with the
        # result. Only meaningful when there is a real command.
        if event.tool_call_id:
            self._pending_cmd[event.tool_call_id] = cmd_sig

        if not cmd_sig:
            yield event
            return

        # How many times has THIS command signature already produced an
        # identical result within the window?  We look for any result signature
        # paired with this command that has hit the threshold.
        counts: dict[str, int] = {}
        for fp in self._recent:
            c, _, r = fp.partition("\x00")
            if c == cmd_sig:
                counts[r] = counts.get(r, 0) + 1

        offending = None
        for r, n in counts.items():
            if n >= self.block_threshold:
                offending = (r, n)
                break

        if offending is None:
            yield event
            return

        r_sig, n = offending
        sample = self._sample.get(cmd_sig + "\x00" + r_sig, "")
        msg = _BLOCK_MESSAGE.format(n=n + 1, sample=sample[: self.sample_chars] or "(empty output)")
        yield dataclasses.replace(event, approved=False, synthetic_result=msg)

    async def on_after_tool(self, event: ToolResultEvent):
        cmd_sig = self._pending_cmd.pop(event.tool_call_id, None)
        if cmd_sig is None:
            # No paired command (e.g. synthetic result from a blocked call, or a
            # tool without a tracked call id) — do not record.
            yield event
            return

        result_text = (event.result or "") + ("\n" + event.error if event.error else "")
        result_sig = _normalise(result_text, self.result_fingerprint_chars)
        fp = cmd_sig + "\x00" + result_sig
        self._recent.append(fp)
        # Keep a readable (un-normalised, truncated) sample for the block msg.
        if self.sample_chars:
            self._sample[fp] = result_text[: self.sample_chars]
        yield event
