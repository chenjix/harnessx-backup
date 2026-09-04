# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RecurringErrorRecoveryProcessor — break error-thrashing with variation.

Failure class (observable, task-agnostic):
    Over the course of a task the agent keeps hitting the SAME underlying
    hard error (a Python/compiler traceback, a ``parse error``, a
    ``*Error: ...`` line, a database ``OperationalError``, etc.), but it
    *varies* the surrounding command each attempt — recompiling, editing,
    re-invoking with slightly different arguments — so no two consecutive
    turns are byte-identical. The error signature nonetheless recurs many
    times across the session while the agent makes no real progress and
    eventually exhausts its step budget or errors out.

Why this is distinct from the guards already in the pipeline:
    * ``LengthTruncationRecoveryProcessor`` fires only on
      ``finish_reason == "length"`` (output-cap runaway, no tool call).
    * ``StuckReasoningRecoveryProcessor`` keys on *consecutive identical*
      assistant narration. It structurally MISSES this class because the
      agent's narration and commands change every turn even though the
      resulting error is the same — the consecutive-run counter never
      climbs (observed max consecutive run 3-4 on the thrash tasks).
    * ``CustomEditToolProcessor`` counts file-write volume, ignoring
      whether a command failed.
    * A per-turn failure guard counts failures *within one turn*; this
      guard tracks the *recurrence of one error fingerprint across the
      whole session*, which is the signal that separates the doomed
      thrash tasks from healthy work.

Mechanism:
    * On each tool result, extract a normalised *hard-error* fingerprint
      (paths/numbers/hex stripped) from the last real error line, skipping
      benign lines (``no output captured`` successes, ``warning`` lines,
      one-off ``command not found`` probes).
    * Maintain a session-wide count per fingerprint.
    * When any single fingerprint has recurred ``recur_threshold`` times,
      arm ONE legible recovery directive that names the recurring error and
      demands a genuinely different diagnostic move (read the full error,
      re-check inputs/assumptions from scratch, isolate the smallest failing
      piece) rather than another superficial retry.
    * A per-fingerprint cooldown prevents re-nudging for the same error
      until it recurs another ``recur_threshold`` times; ``max_nudges``
      bounds total interventions per task.

The nudge is advisory (a single user message, never a +2 insertion); it
never blocks or rewrites tool calls, so it cannot break a task that was
about to self-correct.

Threshold rationale (from the round's trajectory sweep, task-agnostic
statistic — no task literals): across all trajectories the maximum
recurring hard-error count on any PASSING task was 3; the two doomed
error-thrash tasks reached 9 and 10. ``recur_threshold=5`` sits strictly
above every passing task's recurrence and below the failing cluster's, so
the guard fires on the thrash class and on zero passing tasks observed.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


# A real, hard error worth tracking (as opposed to benign warnings / empty
# output). Task-agnostic patterns.
_HARD = re.compile(
    r"traceback \(most recent call last\)"
    r"|parse error"
    r"|segmentation fault"
    r"|[a-z_]*error:"          # e.g. "ValueError:", "error:", "collect2: error:"
    r"|[a-z_]*error\b.*\b(exception|raised|status)"
    r"|operationalerror"
    r"|modulenotfounderror"
    r"|assertionerror",
    re.I,
)

# Lines that look error-ish but are benign noise on this benchmark.
_BENIGN = re.compile(
    r"no output captured"
    r"|^\s*warning"
    r"|command not found"
    r"|tb[ _.-]*self[ _.-]*verify"
    r"|self_verify",
    re.I,
)


def _fingerprint(text: str) -> str:
    """Return a normalised hard-error signature, or '' if none present.

    Scans the result text bottom-up for the last non-benign line that
    matches a hard-error pattern, then strips paths / numbers / hex /
    punctuation so cosmetically-different repeats of the same error
    collapse to one fingerprint.
    """
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if _BENIGN.search(ln):
            continue
        if _HARD.search(ln):
            s = ln.lower()
            s = re.sub(r"0x[0-9a-f]+", " ", s)
            s = re.sub(r"[0-9]+", " ", s)
            s = re.sub(r"/[^ ]+", " ", s)          # drop absolute paths
            s = re.sub(r"[^a-z ]", " ", s)
            s = re.sub(r"\s+", " ", s).strip()
            if len(s) >= 8:
                return s[:80]
    return ""


def _result_text(event: ToolResultEvent) -> str:
    """Best-effort extraction of the textual tool output from the event."""
    parts = []
    r = getattr(event, "result", None)
    if isinstance(r, str):
        parts.append(r)
    elif r is not None:
        parts.append(str(r))
    err = getattr(event, "error", None)
    if err:
        parts.append(str(err))
    blocks = getattr(event, "content_blocks", None)
    if blocks:
        for b in blocks:
            if isinstance(b, dict):
                t = b.get("text") or b.get("content")
                if t:
                    parts.append(str(t))
            else:
                t = getattr(b, "text", None)
                if t:
                    parts.append(str(t))
    return "\n".join(parts)


_NUDGE_FIRST = (
    "REPEATED ERROR: you have now hit the same underlying error "
    "\u2014 '{sig}' \u2014 {n} times across this task, each time with a slightly "
    "different command but the SAME result. Retrying variations of the same "
    "approach is not converging. Before your next command: (1) read the FULL "
    "text of this error, not just the summary; (2) re-verify your assumptions "
    "about the inputs, file paths, and tool invocation from scratch (inspect "
    "the actual data/args, do not assume); (3) reproduce the failure on the "
    "SMALLEST possible input to localise the root cause. Then take a materially "
    "different action \u2014 do not re-run a near-duplicate of what just failed."
)

_NUDGE_REPEAT = (
    "STILL HITTING THE SAME ERROR ('{sig}', now {n} times). The line of attack "
    "you are on does not work. Abandon it. Consider that your model of the "
    "problem may be wrong: re-read the task requirements, check whether a "
    "different tool/library/format is expected, or solve a reduced version end "
    "to end first. Do NOT emit another minor variation of the failing command."
)


class RecurringErrorRecoveryProcessor(MultiHookProcessor):
    """Detect a hard-error fingerprint recurring across a task and force a change."""

    _singleton_group = "tmax_recurring_error_recovery"
    _order = 7

    def __init__(
        self,
        recur_threshold: int = 5,
        max_nudges: int = 2,
    ) -> None:
        # recur_threshold=5 sits strictly above the max recurring hard-error
        # count seen on any passing trajectory in the sweep (3) and below the
        # failing error-thrash cluster (9-10), so the guard cannot fire on the
        # observed passing set.
        self.recur_threshold = max(2, int(recur_threshold))
        self.max_nudges = max(1, int(max_nudges))
        self._counts: dict[str, int] = {}
        self._nudged_at: dict[str, int] = {}  # sig -> count at which we last nudged
        self._nudges = 0
        self._pending_nudge = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._counts = {}
        self._nudged_at = {}
        self._nudges = 0
        self._pending_nudge = ""
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        sig = _fingerprint(_result_text(event))
        if not sig:
            yield event
            return

        self._counts[sig] = self._counts.get(sig, 0) + 1
        n = self._counts[sig]

        last = self._nudged_at.get(sig, 0)
        # Fire when this fingerprint first crosses the threshold, and again only
        # after it recurs another full threshold's worth (cooldown per sig).
        if (
            n >= self.recur_threshold
            and self._nudges < self.max_nudges
            and n - last >= self.recur_threshold
        ):
            short = sig if len(sig) <= 60 else sig[:57] + "..."
            template = _NUDGE_REPEAT if self._nudges >= 1 else _NUDGE_FIRST
            self._pending_nudge = template.format(sig=short, n=n)
            self._nudged_at[sig] = n
            self._nudges += 1

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Contract: never create a +2 user insertion. If the run loop already
        # appended a trailing user message, replace it; otherwise append one.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._counts = {}
        self._nudged_at = {}
        self._nudges = 0
        self._pending_nudge = ""
        yield event
