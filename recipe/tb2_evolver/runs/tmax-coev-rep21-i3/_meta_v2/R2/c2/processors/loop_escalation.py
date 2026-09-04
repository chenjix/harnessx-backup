# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Escalating loop-break processor for the tmax terminal benchmark.

Extends the stock ``LoopDetectionProcessor`` with two behaviours aimed at the
dominant ``budget_exceeded`` failure cluster, where the agent recognises it is
stuck ("I'm stuck in a loop") yet keeps re-issuing the same *kind* of command
until the step budget is exhausted (reward always 0):

1. **Escalating semantic redirect** — the stock name-only (Strategy-2) branch
   emits one static warning and never changes. Here the message escalates as
   the consecutive same-tool run grows, and past ``escalate_at`` it stops being
   a gentle nudge and becomes a decisive "commit or pivot" directive: stop
   re-verifying/re-probing, take exactly one constructive action (fix the root
   cause OR finalise), do not repeat a diagnostic you have already run.

2. **Bounded name-only hard raise** — the stock processor never raises on the
   name-only signal. A truly pathological semantic loop (same tool, varying
   args, no progress) therefore runs to the full budget. Here, once the
   name-only run reaches ``name_threshold`` the processor raises
   ``LoopDetectedError`` (caught cleanly by the run loop as
   ``exit_reason=loop_detected``). Converting a runaway budget burn into an
   earlier clean stop is strictly non-worse on reward (both are 0) and
   reclaims wall-clock/tokens for other tasks.

Both behaviours are purely additive to the parent's exact-match (Strategy-1)
logic, which is unchanged except through its constructor knobs. The processor
only appends text to the current tool result (same mechanism as the parent's
warn), so it never mutates message structure — contract-safe.
"""
from __future__ import annotations

import dataclasses

from harnessx.core.events import ToolResultEvent
from harnessx.core.runloop import LoopDetectedError
from harnessx.processors.control.loop_detection import LoopDetectionProcessor


_ESCALATE_TEMPLATE = (
    "\n\n[LoopDetection] ⛔ STOP. You have called `{tool}` {count} times in a row "
    "without making progress ({pattern}). Repeating the same kind of command is "
    "burning your step budget and will end the task with a failure before you "
    "finish. Do NOT run another diagnostic/verification command you have already "
    "tried. Take exactly ONE of these actions now:\n"
    "  (a) If a required output is still wrong or missing, change the ROOT CAUSE "
    "(edit the config/source/service that is actually broken) — not the way you "
    "inspect it.\n"
    "  (b) If every requirement in the task is already satisfied, finalise and "
    "stop instead of re-checking.\n"
    "Pick one and act; another repeat of this command will terminate the task."
)


class EscalatingLoopBreakProcessor(LoopDetectionProcessor):
    """LoopDetectionProcessor with an escalating semantic redirect + bounded
    name-only hard raise.

    Additional knobs (parent knobs unchanged):
        escalate_at:    name-only consecutive run at which the nudge escalates
                        from the gentle stock message to the decisive
                        commit-or-pivot directive (default 6). Must be >=
                        ``name_warn_threshold`` to be reachable after the first
                        gentle warning.
        name_threshold: name-only consecutive run at which a
                        ``LoopDetectedError`` is raised, ending the task
                        (default 14). Set well above ``escalate_at`` so the
                        agent gets several escalated pushes to pivot before the
                        hard stop fires.
    """

    def __init__(
        self,
        *args,
        escalate_at: int = 6,
        name_threshold: int = 14,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.escalate_at = escalate_at
        self.name_threshold = name_threshold

    async def on_after_tool(self, event: ToolResultEvent):
        # Reconstruct the exact fingerprint the parent stored in on_before_tool.
        fp = self._pending_fp.pop(event.tool_call_id, None)

        if not fp:
            self._fingerprints.append("")
            self._name_fingerprints.append("")
            yield event
            return

        # ── Strategy 1: exact fingerprint (name + inputs) ──────────────
        s1_run = self._consecutive_tail(self._fingerprints, fp) + 1
        self._fingerprints.append(fp)

        if s1_run >= self.threshold:
            raise LoopDetectedError(
                f"Loop detected: identical tool call '{event.tool_name}' "
                f"repeated {s1_run} times consecutively"
            )

        from harnessx.processors.control.loop_detection import _WARN_TEMPLATE

        warning = ""
        if s1_run >= self.warn_threshold:
            warning = _WARN_TEMPLATE.format(count=s1_run, tools=f"`{event.tool_name}`")

        # ── Strategy 2: name-only fingerprint (escalating + bounded raise) ──
        s2_run = self._consecutive_tail(self._name_fingerprints, event.tool_name) + 1
        self._name_fingerprints.append(event.tool_name)

        # Bounded hard raise on a sustained semantic loop.
        if s2_run >= self.name_threshold:
            raise LoopDetectedError(
                f"Loop detected: tool '{event.tool_name}' called {s2_run} times "
                f"consecutively without progress (semantic loop)"
            )

        # Escalate the name-only message once the run is sustained. The exact
        # Strategy-1 warning takes priority when present (it is more specific).
        if not warning and s2_run >= self.escalate_at:
            warning = _ESCALATE_TEMPLATE.format(
                tool=event.tool_name, count=s2_run, pattern=event.tool_name
            )
        elif not warning and s2_run >= self.name_warn_threshold:
            from harnessx.processors.control.loop_detection import _NAME_WARN_TEMPLATE

            warning = _NAME_WARN_TEMPLATE.format(
                tool=event.tool_name, count=s2_run, pattern=event.tool_name
            )

        if warning:
            yield dataclasses.replace(event, result=(event.result or "") + "\n\n" + warning)
        else:
            yield event
