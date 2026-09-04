# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""LengthTruncationRecoveryProcessor (v5 — mechanically break the reasoning spiral).

Motivation (rep21-i3 R1 trajectory evidence)
--------------------------------------------
The R1 config carries a v4 ``LengthTruncationRecoveryProcessor``
(``tmax_length_recovery`` singleton group): on a length-truncated no-tool-call
turn it collapses the runaway reasoning to a short head + tail and rewrites the
run loop's passive "continue from where you left off" nudge into an escalating
actionable text directive.

The R1 trajectories prove the v4 *text* escalation is insufficient for the
dominant budget-drain cluster. On the failing ``budget_exceeded`` tasks the v4
collapse fires correctly (every truncated no-tool-call assistant turn is
collapsed to exactly the head+tail budget, ``tool_calls == 0``) yet the 4B model
keeps emitting pure narration that hits the ``max_tokens`` cap turn after turn,
never reaching a tool call:

  * ``task_000015_89886d8d`` — 11 passive "cut off by the token limit" continue
    turns before the first real Bash call; ``exit_reason=budget_exceeded``.
  * ``task_001717_a9c46d8d`` — 16 truncation/continue turns interleaved;
    ``exit_reason=budget_exceeded``.
  * ``task_001706_24462a09`` — 9 truncation/continue turns; ``budget_exceeded``.
  * ``task_000936_2a78f3ca`` — 7; ``task_001898_471c0535`` — 6; all
    ``budget_exceeded``.

An audit confirms the v4 text nudge appears ZERO times in any final transcript
even though the collapse fired: the model narrates straight past the text
directive ("write NO analysis, emit ONE command") until the cap. A reminder the
model narrates past is not a fix. What breaks the momentum is **fresh tool
output the model did not author** — once a real tool result lands in context,
verbose models reliably switch from open-ended reasoning back to acting on the
concrete output.

v5 change (Control, same lever family, new evidence)
----------------------------------------------------
Keep everything v4 does (collapse + escalating text nudge) and add a
**mechanical forced-action escalation**: once ``force_action_threshold``
*consecutive* truncations have accumulated, ``on_after_model`` injects a real
``Bash`` tool call — a short, fully generic workspace-state snapshot. The run
loop executes it and feeds the output back, so the next turn has fresh ground
truth to react to instead of re-priming the same unfinished thought. It fires at
most once per streak (the injected tool call clears the streak, exactly like any
other tool-calling turn), so it adds no per-step regression surface and costs one
bounded Bash round-trip only on runs that are already spiralling and would
otherwise burn the whole budget.

Generality
----------
The snapshot command contains **no task ids, paths, ports, process names, or any
literal from any training task** — it enumerates the current working directory,
recently-modified files, and running user processes. It is generic shell-state
discipline, not domain knowledge, and keys purely on ``finish_reason`` /
``tool_calls`` / ``content`` length plus the harness's own passive-nudge text —
so it is benchmark-agnostic.

Wiring
------
Reuses the R1 singleton group ``tmax_length_recovery`` so it *replaces* the R1
v4 processor rather than firing a second, conflicting recovery layer.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
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

# Repeated occurrence: hard reset. When a large file write keeps getting
# truncated, a single big heredoc cannot fit under the output cap — steer the
# model toward SMALL payloads: incremental appends, in-place edits (sed/patch),
# or short inspection commands, never a full-file rewrite in one turn.
_NUDGE_REPEAT = (
    "STOP. Your last few turns were all cut off mid-response because the output "
    "is larger than the per-turn token limit. Write NO analysis this turn — emit "
    "exactly ONE Bash tool call and nothing else. Do NOT try to write or rewrite "
    "a whole file in one command; that is what keeps getting truncated. Instead "
    "make a SMALL change that fits: edit in place (`sed -i`), append a chunk "
    "(`cat >> file <<'EOF' ... EOF` with only a few lines), or run ONE short "
    "inspection command (ls, cat, or the failing script) so the next step has "
    "fresh output to act on. One tool call. Zero prose."
)

# Message paired with the mechanically injected snapshot tool call. Tells the
# model the reasoning loop was broken for it and that concrete state now follows.
_FORCE_ACTION_MSG = (
    "You were stuck emitting reasoning that repeatedly exceeded the output token "
    "limit without ever running a command, so a workspace snapshot was run for "
    "you. Above is real, current state — not your narration. React to THIS "
    "output with one small concrete Bash command (a targeted edit or a single "
    "inspection). Do not resume the abandoned reasoning."
)

# A fully generic workspace-state snapshot. No task ids/paths/process names.
# `|| true` and `2>&1` keep it silent/successful even when nothing matches.
_SNAPSHOT_CMD = (
    "echo '=== WORKSPACE SNAPSHOT (auto) ==='; "
    "echo '--- cwd + recent files under /home/user ---'; "
    "pwd; "
    "ls -lat /home/user 2>&1 | head -n 25 || true; "
    "find /home/user -maxdepth 3 -type f -mmin -30 2>&1 | head -n 25 || true; "
    "echo '--- running user processes ---'; "
    "ps -eo pid,ppid,stat,comm --sort=pid 2>&1 "
    "| awk 'NR==1 || ($2!=2 && $1!=2)' | head -n 30 || true; "
    "echo '=== END SNAPSHOT ==='"
)


class LengthTruncationRecoveryProcessor(MultiHookProcessor):
    """Break the max_tokens reasoning-spiral loop and redirect the model to act.

    Parameters
    ----------
    repeat_threshold:
        Number of *consecutive* truncations at (or above) which the escalated
        text nudge is used instead of the gentle one. Default 2.
    force_action_threshold:
        Number of *consecutive* truncations at (or above) which the processor
        stops nudging with text and mechanically injects a real Bash snapshot
        tool call so fresh tool output lands in context. Must be >= 1; default
        3. Fires at most once per streak (the injected tool call clears the
        streak).
    head_chars / tail_chars:
        How much of a runaway content blob to keep when collapsing it. Default
        keeps a small head and NO tail so the re-seeding mid-reasoning
        continuation is discarded.
    content_char_threshold:
        Secondary trigger: assistant ``content`` length (chars) at or above
        which a no-tool-call turn is treated as a runaway truncation even when
        ``finish_reason`` is not reported as ``"length"``.
    """

    _singleton_group = "tmax_length_recovery"
    _order = 5

    def __init__(
        self,
        repeat_threshold: int = 2,
        force_action_threshold: int = 3,
        head_chars: int = _HEAD_CHARS,
        tail_chars: int = _TAIL_CHARS,
        content_char_threshold: int = _CONTENT_CHAR_THRESHOLD,
    ) -> None:
        self.repeat_threshold = max(1, int(repeat_threshold))
        self.force_action_threshold = max(1, int(force_action_threshold))
        self.head_chars = max(0, int(head_chars))
        self.tail_chars = max(0, int(tail_chars))
        self.content_char_threshold = max(1, int(content_char_threshold))
        self._consecutive: int = 0
        self._pending_nudge: str = ""
        self._pending_force_msg: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._consecutive = 0
        self._pending_nudge = ""
        self._pending_force_msg = ""
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        content = event.content or ""
        no_tool_call = not event.tool_calls
        # Primary trigger: explicit length finish. Secondary: an oversized
        # no-tool-call content blob (covers backends that don't report length).
        length_truncated = no_tool_call and (
            event.finish_reason == "length"
            or len(content) >= self.content_char_threshold
        )
        if not length_truncated:
            # Any normal / tool-calling turn clears the streak.
            self._consecutive = 0
            self._pending_nudge = ""
            self._pending_force_msg = ""
            yield event
            return

        self._consecutive += 1

        # Escalation ladder:
        #   >= force_action_threshold : mechanically inject a real Bash snapshot
        #                               so fresh tool output breaks the spiral.
        #   >= repeat_threshold       : hard text reset (small-payload strategy).
        #   else                      : gentle text redirect.
        if self._consecutive >= self.force_action_threshold:
            # Collapse the runaway turn first (discard the re-seeding middle).
            collapsed_event = self._maybe_collapse(event, content)
            # Arm the paired user message for the next on_before_model, and
            # inject a REAL Bash diagnostic tool call now. The run loop executes
            # it; the injected tool call also clears the streak on the next
            # on_after_model, so this fires at most once per spiral.
            self._pending_nudge = ""
            self._pending_force_msg = _FORCE_ACTION_MSG
            snapshot = ToolCall(
                id=f"lr-{uuid.uuid4().hex[:8]}",
                name="Bash",
                input={"command": _SNAPSHOT_CMD},
            )
            yield dataclasses.replace(collapsed_event, tool_calls=(snapshot,))
            return

        self._pending_nudge = (
            _NUDGE_REPEAT
            if self._consecutive >= self.repeat_threshold
            else _NUDGE_FIRST
        )
        yield self._maybe_collapse(event, content)

    def _maybe_collapse(self, event: ModelResponseEvent, content: str):
        """Collapse a runaway turn to a short head + terminal marker (no tail)."""
        keep_budget = self.head_chars + self.tail_chars + len(_TRUNC_MARKER)
        if len(content) > keep_budget:
            collapsed = content[: self.head_chars] + _TRUNC_MARKER
            if self.tail_chars:
                collapsed = collapsed + content[-self.tail_chars :]
            return dataclasses.replace(event, content=collapsed)
        return event

    async def on_before_model(self, event: BeforeModelEvent):
        # Forced-action path: the snapshot tool call was injected on the prior
        # on_after_model; its result is now in history. Append exactly one user
        # message pointing the model at that fresh output. The last role here is
        # a tool result, so a single append is contract-legal (+1).
        if self._pending_force_msg:
            msg = self._pending_force_msg
            self._pending_force_msg = ""
            yield dataclasses.replace(
                event,
                messages=event.messages + (Message(role="user", content=msg),),
            )
            return

        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""

        messages = event.messages
        if messages and messages[-1].role == "user":
            # Contract-safe path: rewrite ONLY the last user message's content
            # (len_delta == 0). Overwrites the run loop's passive re-priming
            # nudge with an actionable directive; history is otherwise untouched.
            last = messages[-1]
            existing = last.content or ""
            if _PASSIVE_MARKER in existing:
                new_content = nudge
            else:
                new_content = nudge + "\n\n" + existing
            new_last = dataclasses.replace(last, content=new_content)
            yield dataclasses.replace(event, messages=messages[:-1] + (new_last,))
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
        self._pending_force_msg = ""
        yield event
