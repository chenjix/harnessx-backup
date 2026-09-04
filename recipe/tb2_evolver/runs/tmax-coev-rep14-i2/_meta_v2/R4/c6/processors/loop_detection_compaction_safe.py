# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Compaction-safe loop detector.

Fixes a design flaw in the stock ``LoopDetectionProcessor`` that lets a
tight identical-call loop escape termination when the loop *itself* drives
context compaction.

Background
----------
``LoopDetectionProcessor`` counts the consecutive run of identical tool-call
fingerprints and raises ``LoopDetectedError`` once the run reaches
``threshold``. To avoid false positives from *stale* pre-compaction
fingerprints, its ``on_step_start`` hook **fully clears** both fingerprint
windows whenever it detects a message-count drop of at least
``compaction_drop_threshold`` between steps.

That reset is too aggressive. When an agent is stuck emitting the *same*
Bash call over and over, every repeated turn re-narrates and re-grows the
context, which repeatedly trips ``CompactionProcessor``. Each compaction then
wipes the fingerprint window *mid-run*, so the consecutive counter is reset to
zero before it can ever reach ``threshold``. The result: the loop detector is
silently defeated in exactly the scenario it exists for — verified empirically
on this benchmark where tasks issued 9–33 byte-identical consecutive calls yet
never produced a clean ``loop_detected`` exit, burning the entire step budget
instead (``exit_reason=budget_exceeded``/``error``).

Fix
---
On a compaction-driven message drop we no longer zero the run. Instead we
**preserve the trailing consecutive identical run** at the tail of each
fingerprint window and drop only the older, heterogeneous entries. This keeps
the original anti-stale-false-positive intent (varied pre-compaction
fingerprints are discarded) while ensuring a genuine ongoing identical loop
still accumulates toward ``threshold`` across compaction boundaries.

The change is confined to ``on_step_start``; every other hook, the raise
threshold, the warn escalation, and the exit semantics are inherited unchanged
from the stock processor. Registered under the same ``_singleton_group`` and
``_order`` so it drops into the same pipeline slot.
"""
from __future__ import annotations

from collections import deque

from harnessx.core.events import StepStartEvent
from harnessx.processors.control.loop_detection import LoopDetectionProcessor


def _tail_run(window: "deque[str]") -> int:
    """Length of the run of equal entries at the tail of *window* (0 if empty)."""
    if not window:
        return 0
    last = window[-1]
    if last == "":
        # An empty fingerprint marks a step that broke the run; nothing to keep.
        return 0
    count = 0
    for past in reversed(window):
        if past == last:
            count += 1
        else:
            break
    return count


class CompactionSafeLoopDetectionProcessor(LoopDetectionProcessor):
    """``LoopDetectionProcessor`` whose compaction reset preserves an active loop.

    Identical constructor signature and defaults as the base class, so it is a
    drop-in replacement in the config. Only ``on_step_start`` differs.
    """

    _singleton_group = "loop_detection"
    _order = 20

    async def on_step_start(self, event: StepStartEvent):
        # New run → full reset (delegates to base semantics for run_id change).
        if event.run_id != self._current_run_id:
            self._reset()
            self._current_run_id = event.run_id

        current_count = len(event.messages)
        dropped = (
            self._prev_message_count > 0
            and self._prev_message_count - current_count >= self.compaction_drop_threshold
        )

        if dropped:
            # Compaction fired. Instead of wiping the windows (which would zero an
            # in-progress identical loop), keep only the trailing consecutive run
            # so a genuine loop survives across the compaction boundary while
            # older heterogeneous fingerprints — the real stale-false-positive
            # risk — are discarded.
            exact_keep = _tail_run(self._fingerprints)
            name_keep = _tail_run(self._name_fingerprints)

            if exact_keep > 0:
                tail = list(self._fingerprints)[-exact_keep:]
                self._fingerprints = deque(tail, maxlen=self.window_size)
            else:
                self._fingerprints.clear()

            if name_keep > 0:
                name_tail = list(self._name_fingerprints)[-name_keep:]
                self._name_fingerprints = deque(name_tail, maxlen=self.window_size)
            else:
                self._name_fingerprints.clear()

            # Pending per-call fingerprints refer to calls not yet resolved this
            # step; they are safe to keep, but clear defensively as the base did.
            self._pending_fp.clear()

        self._prev_message_count = current_count
        yield event
