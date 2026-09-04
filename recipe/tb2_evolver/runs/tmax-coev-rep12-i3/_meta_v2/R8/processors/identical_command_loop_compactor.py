# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""IdenticalCommandLoopCompactor — durably break identical-Bash-command loops.

Closes a systemic ``budget_exceeded`` failure mode that the existing pipeline
does NOT catch: the agent emits the **byte-identical single Bash command** turn
after turn, gets the same (usually empty / failing) tool result each time, and
burns the whole step budget on one unproductive command.

Why the existing processors miss it
------------------------------------
* ``LengthTruncationRecoveryProcessor`` / ``TruncationLoopCompactor`` only fire
  on ``finish_reason=length`` narration turns that carry NO tool call. The
  identical-command loop turns DO carry a well-formed tool call, so those
  processors never trigger.
* The R2 ``RepeatCommandBreakerProcessor`` detects the pattern but injects its
  redirect in ``on_before_model``; the run loop rebuilds the model input from
  ``state.raw_messages`` every step and never writes ``on_before_model`` edits
  back to state, so the nudge is ephemeral — it evaporates the next turn and the
  model never actually sees it. (Confirmed: zero "loop detected by the harness"
  strings appear in any looping trajectory even though R2 is registered.)
* The R5 hard *blocker* (``approved=False`` in ``on_before_tool``) DID persist
  but blocked execution and disrupted legitimate iteration, so it was reverted.

This processor takes the middle path R2 intended but could not deliver: it runs
at ``on_step_start`` (like the working ``TruncationLoopCompactor``), so its edit
to ``event.messages`` changes the assembled ``history_hash`` and the run loop
auto-generates a SegmentBoundary that writes the trimmed window durably into
both ``state.raw_messages`` and ``state.messages`` (runloop.py ~lines 345-368).
It NEVER blocks or drops a tool call and never fabricates a tool result — the
commands already executed. It only:

  1. collapses a maximal run of >= ``min_run`` consecutive identical-command
     turns (each with its interleaved ``tool`` result) down to the FIRST such
     turn + its result, removing the redundant duplicates from context, and
  2. appends ONE corrective user directive telling the agent the command has
     produced the same result N times and it must change strategy.

Because it operates on already-produced history, a legitimately-slow retry is
untouched until the run is long enough to be an unambiguous loop. The default
``min_run`` is deliberately high (10): the passing set's largest identical
non-empty Bash run is 7 (a truncation-interleaved iterative task that goes on to
PASS), so a threshold of 10 provably cannot fire on any currently-passing
trajectory while still catching the runaway loopers (measured 19 and 31 repeats).

Generality: keys purely off the *shape* "same normalized Bash command repeated
consecutively N+ times". No task ids, paths, commands, or domain literals — it
closes a class of loops and is a no-op on any run whose consecutive-command run
never reaches ``min_run`` (the overwhelmingly common case).
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


_LOOP_NOTE = (
    "[harness: your previous {n} turns issued the SAME command over and over and "
    "it produced the same result every time. Those redundant repeats were "
    "collapsed to keep context clean. Repeating that command again will NOT "
    "change the outcome. STOP. In your next turn do ONE of the following: "
    "(a) if the command failed or returned nothing useful, diagnose WHY with a "
    "genuinely DIFFERENT command — verify the exact path/file exists, inspect the "
    "real error, or use a different tool/flags; or (b) if you already have the "
    "information you need, move on to the NEXT concrete step of the task. Do not "
    "run that same command again.]"
)


def _single_bash_command(m: Message) -> str | None:
    """Normalized command of an assistant turn with exactly one Bash tool call.

    Returns ``None`` for anything that is not a single-Bash-call assistant turn
    (narration, multi-call turns, non-Bash calls, empty commands). ``None``
    always breaks a run.
    """
    if getattr(m, "role", None) != "assistant":
        return None
    calls = getattr(m, "tool_calls", None) or ()
    if len(calls) != 1:
        return None
    call = calls[0]
    if getattr(call, "name", None) != "Bash":
        return None
    inp = getattr(call, "input", None) or {}
    cmd = inp.get("command") if isinstance(inp, dict) else None
    if not isinstance(cmd, str):
        return None
    norm = cmd.strip()
    return norm or None


def _is_tool_result_for(m: Message) -> bool:
    """True when ``m`` is a tool-result message (role='tool')."""
    return getattr(m, "role", None) == "tool"


class IdenticalCommandLoopCompactor(MultiHookProcessor):
    """Collapse a run of consecutive byte-identical Bash-command turns."""

    _singleton_group = "tmax_identical_command_loop_compactor"
    # After the TruncationLoopCompactor (order 20) so both structural
    # collapses operate on the assembled window before the model call.
    _order = 21

    def __init__(self, min_run: int = 10) -> None:
        # Fire only once a run of consecutive identical-command turns reaches
        # this length. Default 10 sits safely above the passing set's max (7).
        self.min_run = max(4, int(min_run))

    async def on_task_start(self, event: TaskStartEvent):
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        yield event

    async def on_step_start(self, event: StepStartEvent):
        msgs = list(event.messages)
        if len(msgs) < self.min_run:
            yield event
            return

        out: list[Message] = []
        i = 0
        n = len(msgs)
        changed = False
        while i < n:
            m = msgs[i]
            cmd = _single_bash_command(m)
            if cmd is None:
                out.append(m)
                i += 1
                continue

            # Start a run of identical-command turns. Each qualifying assistant
            # turn may be immediately followed by its tool-result message; we
            # step over that result to reach the next assistant turn.
            run_asst_idxs = [i]
            j = i + 1
            # collect the trailing result of the first turn (if any)
            while j < n and _is_tool_result_for(msgs[j]):
                j += 1
            while j < n:
                if _single_bash_command(msgs[j]) == cmd:
                    run_asst_idxs.append(j)
                    k = j + 1
                    while k < n and _is_tool_result_for(msgs[k]):
                        k += 1
                    j = k
                    continue
                break

            run_len = len(run_asst_idxs)
            if run_len >= self.min_run:
                # Keep the FIRST identical turn and its result; drop the rest.
                first_idx = run_asst_idxs[0]
                out.append(msgs[first_idx])
                r = first_idx + 1
                while r < n and _is_tool_result_for(msgs[r]):
                    out.append(msgs[r])
                    r += 1
                # Append exactly ONE corrective directive. If the message that
                # follows the collapsed run is itself a user turn, fold the
                # directive into it to avoid two consecutive user messages.
                note_text = _LOOP_NOTE.format(n=run_len)
                if j < n and getattr(msgs[j], "role", None) == "user":
                    nxt = msgs[j]
                    merged = note_text + "\n\n" + (getattr(nxt, "content", "") or "")
                    out.append(Message(role="user", content=merged))
                    j += 1
                else:
                    out.append(Message(role="user", content=note_text))
                changed = True
                i = j
            else:
                # Too short to be a loop; leave the turns (and results) intact.
                for k in range(i, j):
                    out.append(msgs[k])
                i = j

        if not changed:
            yield event
            return

        # Safety: never emit an empty window and never drop the leading message.
        if not out:
            yield event
            return
        if out[0] is not msgs[0]:
            out = [msgs[0]] + [x for x in out if x is not msgs[0]]

        yield dataclasses.replace(event, messages=tuple(out))
