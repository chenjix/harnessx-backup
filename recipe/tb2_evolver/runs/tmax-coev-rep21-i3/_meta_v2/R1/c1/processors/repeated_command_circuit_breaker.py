# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RepeatedCommandCircuitBreaker for Tmax / TB2-style single-Bash agents.

Closes the single most catastrophic failure mode observed on this benchmark:
the model enters a *degenerate action loop* where it issues the **exact same
Bash command** over and over, receives the **exact same output** every time,
narrates "the system is detecting repeated commands / let me try a different
approach", and then re-issues the identical command anyway. It never makes
forward progress and eventually exits ``budget_exceeded`` with none of the
required deliverables written.

Why the existing guards do not stop it
--------------------------------------
The pipeline already contains soft *text* nudges — ``CustomEditToolProcessor``
appends an "[EditDetection] ... step back" line to the tool result, and
``PostCompactionRefreshProcessor`` re-injects the workspace state. Trajectory
evidence shows the model **reads those warnings, acknowledges them verbatim,
and then repeats the identical command regardless**. A warning appended to an
otherwise-identical tool result does not change the reinforcement: the model
still sees the same command produce (essentially) the same output.

What this processor does differently
-------------------------------------
It intercepts the repeated call *before execution* (``on_before_tool`` →
``approved=False``) and injects a **synthetic, escalating** result in place of
the real (identical) one. This breaks the loop mechanically in two ways:

1. The command does not run, so no wall-clock / tokens are spent re-deriving
   the same output.
2. The observation the model receives is **different from before** and
   escalates across strikes, denying the model the identical stimulus that
   was reinforcing the loop, and redirecting it toward the task deliverables.

The guard only fires on *consecutive identical* commands (a genuine stall),
so it is safe for legitimate repeated polling of distinct state (each poll
that returns new information keeps changing the command or is interleaved with
other commands). Even in the rare case where a passing run harmlessly repeats
one command after the real work is done, blocking that repeat only reclaims
budget — it cannot un-write an already-correct output file.

This is task-agnostic: it keys purely on the structural repetition signal, not
on any task content.
"""

from __future__ import annotations

import hashlib
import re

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor


def _normalize_command(cmd: str) -> str:
    """Collapse insignificant whitespace so trivially-reformatted repeats of the
    same command still register as identical. Keeps semantic content intact."""
    if not cmd:
        return ""
    # Normalize runs of whitespace (incl. newlines) to single spaces and strip.
    return re.sub(r"\s+", " ", cmd).strip()


_STRIKE_1 = (
    "[CircuitBreaker] BLOCKED — this Bash command was NOT executed.\n"
    "You have now issued the *identical* command {n} times in a row and it "
    "returns the same output every time. Repeating it cannot produce new "
    "information. Running it again is banned.\n"
    "Do something concrete and DIFFERENT next: either (a) change the command "
    "materially (different tool, different flags, different file), or (b) if you "
    "have already learned what this command tells you, STOP inspecting and start "
    "producing the required deliverable(s) — write the output file(s) to the "
    "exact path(s) named in the task. Issue ONE new, different Bash command now."
)

_STRIKE_2 = (
    "[CircuitBreaker] STILL BLOCKED — the identical command is refused again "
    "({n} repeats).\n"
    "You are stuck in a loop. Abandon the current line of attack entirely. Do "
    "NOT re-run any variant of that command and do NOT narrate about it.\n"
    "Re-read the task's required outputs. In your next turn issue ONE short Bash "
    "command that makes tangible progress toward writing a required output file "
    "(for example: create the target script with a heredoc, or list the paths "
    "the task asks you to produce with `ls -l`). One concrete, different command."
)

_STRIKE_3 = (
    "[CircuitBreaker] HARD STOP — command refused ({n} repeats).\n"
    "This exact command has been blocked multiple times and you keep returning to "
    "it. That approach is a dead end. Completely switch strategy: pick the single "
    "most important required output file named in the task and write it NOW with a "
    "single Bash heredoc (`cat > /path/to/file << 'EOF' ... EOF`), even a minimal "
    "first version. Produce the file. Do not run the blocked command again."
)


class RepeatedCommandCircuitBreaker(MultiHookProcessor):
    """Hard-break identical-command repetition loops by refusing to execute the
    repeated call and injecting an escalating redirect as its synthetic result."""

    _singleton_group = "repeated_command_circuit_breaker"
    # Run early in before_tool so the block short-circuits execution before any
    # write-detection / edit-count bookkeeping in later processors.
    _order = 2

    def __init__(self, repeat_threshold: int = 3, reset_after_block: bool = True) -> None:
        # Fire once the command has been issued `repeat_threshold` times in a row
        # (i.e. after `repeat_threshold - 1` identical repeats following the first).
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.reset_after_block = bool(reset_after_block)
        self._last_fp: str | None = None
        self._streak: int = 0  # consecutive count of the current fingerprint
        self._strikes: int = 0  # how many times we've blocked the *current* run of repeats

    async def on_task_start(self, event: TaskStartEvent):
        self._last_fp = None
        self._streak = 0
        self._strikes = 0
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name != "Bash":
            # A non-Bash action counts as forward motion; reset the streak.
            self._last_fp = None
            self._streak = 0
            self._strikes = 0
            yield event
            return

        cmd = ""
        try:
            cmd = event.tool_input.get("command", "") or ""
        except AttributeError:
            cmd = ""
        norm = _normalize_command(cmd)
        fp = hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest() if norm else ""

        if fp and fp == self._last_fp:
            self._streak += 1
        else:
            # A materially different command — the loop is broken by the agent
            # itself; reset all counters and let it run.
            self._last_fp = fp
            self._streak = 1
            self._strikes = 0
            yield event
            return

        if self._streak >= self.repeat_threshold:
            self._strikes += 1
            if self._strikes >= 3:
                msg = _STRIKE_3.format(n=self._streak)
            elif self._strikes == 2:
                msg = _STRIKE_2.format(n=self._streak)
            else:
                msg = _STRIKE_1.format(n=self._streak)

            if self.reset_after_block:
                # Give the agent a clean slate so a genuinely new command isn't
                # immediately re-blocked, but keep the strike escalation via the
                # fingerprint check below.
                self._streak = 0
                # Keep _last_fp so that if it *immediately* repeats the same
                # command again we still recognise it and escalate strikes.

            import dataclasses

            yield dataclasses.replace(event, approved=False, synthetic_result=msg)
            return

        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._last_fp = None
        self._streak = 0
        self._strikes = 0
        yield event
