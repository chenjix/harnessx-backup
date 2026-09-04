# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Output-identity loop detection.

The in-tree ``LoopDetectionProcessor`` fingerprints tool *inputs* (name +
serialised arguments). That misses a common failure shape on weak models:
the agent keeps issuing *slightly different* commands that all produce
**byte-identical output** — e.g. it wraps the same failing shell call in a
here-doc, then in ``python3 -c``, then adds an ``os.makedirs`` — while the
underlying error text never changes. Because the input bytes differ each
time, the input-based detector's consecutive-run counter keeps resetting and
the run burns budget on a loop that is, semantically, going nowhere.

This processor is the dual: it fingerprints the tool *result* and counts how
many times in a row the **same tool** returned **byte-identical output**.
Output-identity is a strong "nothing changed" signal — legitimate work almost
never produces the exact same output many times consecutively, since each step
normally advances state. It warns from ``warn_threshold`` (surfacing the
concrete fact that the output has not changed, which is more actionable than a
generic "you are looping" nudge) and raises ``LoopDetectedError`` at
``threshold`` to reclaim budget on hopeless output-loops.

It is deliberately complementary to and independent of the input-based
detector: either can fire first depending on whether the loop is input- or
output-shaped. Both map ``LoopDetectedError`` to ``exit_reason=loop_detected``
(not ``error``), so neither trips the replay crash gate.
"""
from __future__ import annotations

import dataclasses
import hashlib
from collections import deque

from harnessx.core.events import (
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor
from harnessx.core.runloop import LoopDetectedError


_WARN_TEMPLATE = (
    "\n\n[OutputLoopDetection] ⚠️  `{tool}` has returned the *exact same output* "
    "{count} times in a row. The command text may differ but the result is "
    "byte-identical, so nothing is changing. Re-issuing variations of the same "
    "call will not help — re-read the tool output above, question the assumption "
    "behind it (wrong flag / wrong path / missing precondition), and take a "
    "fundamentally different action, or move on to the next required step."
)


class OutputLoopDetectionProcessor(MultiHookProcessor):
    """Detect loops by tool-*output* identity rather than tool-input identity.

    Counts the consecutive-run length of byte-identical ``(tool_name, result)``
    fingerprints at the tail of a sliding window. Warns from ``warn_threshold``
    and raises :class:`LoopDetectedError` at ``threshold``.

    Args:
        window_size:               Sliding window for the fingerprint deque.
        warn_threshold:            Consecutive identical-output count that
                                   injects a warning into the tool result.
        threshold:                 Consecutive identical-output count that
                                   raises ``LoopDetectedError`` to reclaim
                                   budget. Set higher than ``warn_threshold``
                                   so the agent gets several warned chances to
                                   self-correct before the run is terminated.
        min_output_len:            Ignore outputs shorter than this (in chars).
                                   Very short outputs (``""``, ``"ok"``, single
                                   digits) legitimately recur and must not
                                   trip the detector.
        compaction_drop_threshold: Message-count drop that signals compaction
                                   and clears the fingerprint window.
    """

    _singleton_group = "output_loop_detection"
    _order = 21  # just after the input-based LoopDetectionProcessor (_order=20)

    def __init__(
        self,
        window_size: int = 12,
        warn_threshold: int = 3,
        threshold: int = 6,
        min_output_len: int = 12,
        compaction_drop_threshold: int = 5,
    ):
        self.window_size = window_size
        self.warn_threshold = warn_threshold
        self.threshold = threshold
        self.min_output_len = min_output_len
        self.compaction_drop_threshold = compaction_drop_threshold

        self._fingerprints: deque[str] = deque(maxlen=window_size)
        self._current_run_id: str = ""
        self._prev_message_count: int = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(result: str | None) -> str:
        """Strip trailing warnings injected by sibling loop detectors so their
        text does not defeat output-identity matching, and trim whitespace."""
        if not result:
            return ""
        text = result
        for marker in ("[LoopDetection]", "[OutputLoopDetection]"):
            idx = text.find(marker)
            if idx != -1:
                text = text[:idx]
        return text.strip()

    def _fingerprint(self, tool_name: str, result: str | None) -> str:
        body = self._normalise(result)
        if len(body) < self.min_output_len:
            return ""  # too short / empty → never a loop signal
        payload = f"{tool_name}\x00{body}"
        return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:16]

    @staticmethod
    def _consecutive_tail(window: deque, fp: str) -> int:
        count = 0
        for past in reversed(window):
            if past == fp:
                count += 1
            else:
                break
        return count

    def _reset(self) -> None:
        self._fingerprints.clear()
        self._prev_message_count = 0

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        self._current_run_id = event.run_id
        yield event

    async def on_step_start(self, event: StepStartEvent):
        if event.run_id != self._current_run_id:
            self._reset()
            self._current_run_id = event.run_id
        current_count = len(event.messages)
        if (
            self._prev_message_count > 0
            and self._prev_message_count - current_count >= self.compaction_drop_threshold
        ):
            self._fingerprints.clear()
        self._prev_message_count = current_count
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        fp = self._fingerprint(event.tool_name, event.result)

        # Empty / too-short outputs break the run without contributing.
        if not fp:
            self._fingerprints.append("")
            yield event
            return

        run_len = self._consecutive_tail(self._fingerprints, fp) + 1
        self._fingerprints.append(fp)

        if run_len >= self.threshold:
            raise LoopDetectedError(
                f"Output loop detected: '{event.tool_name}' returned identical "
                f"output {run_len} times consecutively"
            )

        if run_len >= self.warn_threshold:
            warning = _WARN_TEMPLATE.format(tool=f"`{event.tool_name}`", count=run_len)
            yield dataclasses.replace(event, result=(event.result or "") + warning)
            return

        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
