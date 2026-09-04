# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""CommandRepetitionRecoveryProcessor for Tmax / TB2-style agents.

Closes a systemic loop failure that both existing guards miss:

* ``LengthTruncationRecoveryProcessor`` fires only on
  ``finish_reason == "length"`` with no tool call — these loops finish
  normally and carry tool calls.
* ``ContentRepetitionRecoveryProcessor`` (R2) fingerprints assistant
  *narration* content; the observed R2 loops emit long, re-worded
  narration turn after turn (the model rephrases "I'm stuck, let me try
  a different approach" each time) so no two consecutive turns share the
  same content prefix — that guard never armed on the loop cluster.
* ``CustomEditToolProcessor`` counts only *file-write* commands
  (``cat > file`` / editor writes); these loops re-issue *diagnostic /
  compile / probe* commands (``curl ...``, ``g++ ...``, ``openssl ...``,
  ``echo ... | ...``) which it ignores.

The robust invariant across the whole loop cluster is at the **tool-call
layer**: the agent re-issues the *same Bash command string* many times
(observed 10x–34x identical commands within a single 80-step task) while
producing no new state and never writing the required output. This
processor keys on the normalized Bash command string — orthogonal to the
R2 content fingerprint — and after a command has been issued
``repeat_threshold`` times arms an escalating corrective nudge that tells
the model to stop re-running that command and either gather genuinely new
evidence or write the required output to its exact task path.

Detection is over a rolling recent window (``window`` most-recent
commands), so it also catches the *oscillating* variant where the agent
alternates between a small handful of commands rather than repeating one
verbatim back-to-back.

Message-contract safe: mutates ``event.messages`` by at most +1 user turn
per armed nudge (replacing a trailing user message the run loop already
appended, rather than adding a second). No task-specific literals.
"""

from __future__ import annotations

import dataclasses
import re
from collections import deque

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

_WS = re.compile(r"\s+")

_NUDGE_FIRST = (
    "You have now run essentially the SAME command several times and it keeps "
    "returning the same result — you are looping, not progressing. Do not run "
    "that command again. Change tactic concretely to get NEW information: inspect "
    "a different file or path, print intermediate values, dump raw bytes, reduce "
    "the problem to a smaller isolated test, or read the exact error more "
    "carefully. Run one command you have not run before."
)

_NUDGE_REPEAT = (
    "STOP — you are stuck re-running the same command and burning your step "
    "budget with no new progress. Re-running it will not change the outcome. "
    "Decide right now between exactly two moves and take one: (a) if you have a "
    "partial but plausible result, WRITE the required output file(s) to the "
    "EXACT path named in the task, then confirm each exists with `ls -l`; or "
    "(b) gather genuinely new evidence with a single command you have not tried "
    "before. Do not repeat the command that is looping."
)


def _normalize_cmd(cmd: str) -> str:
    """Whitespace-normalized, length-bounded command signature.

    Bounded so a huge here-doc file-write is compared by its leading bytes
    (which are stable across re-issues) rather than allocating on every call.
    """
    if not cmd:
        return ""
    return _WS.sub(" ", cmd.strip())[:400]


class CommandRepetitionRecoveryProcessor(MultiHookProcessor):
    """Break a repeated-Bash-command loop and redirect the agent to act."""

    _singleton_group = "tmax_command_repetition_recovery"
    # After the content-repetition guard (_order=6) so the two orthogonal
    # detectors don't both fire on the same turn; this one keys on tool args.
    _order = 7

    def __init__(
        self,
        repeat_threshold: int = 4,
        escalate_threshold: int = 7,
        window: int = 12,
        min_cmd_chars: int = 3,
    ) -> None:
        # Times the same command must appear in the recent window before the
        # first nudge. 4 is well clear of legitimate 2-3x retries (build →
        # fix → rebuild) seen on passing tasks.
        self.repeat_threshold = max(3, int(repeat_threshold))
        # Occurrences before the hard "stop / write output" nudge.
        self.escalate_threshold = max(self.repeat_threshold + 1, int(escalate_threshold))
        # Rolling window of most-recent commands the counter looks back over,
        # so oscillation between a few commands also trips.
        self.window = max(self.escalate_threshold, int(window))
        # Ignore trivially short commands (e.g. `ls`, `pwd`) so ordinary
        # navigation does not trip the guard.
        self.min_cmd_chars = max(0, int(min_cmd_chars))
        self._recent: deque[str] = deque(maxlen=self.window)
        self._pending_nudge: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._recent = deque(maxlen=self.window)
        self._pending_nudge = ""
        yield event

    def _extract_commands(self, event: ModelResponseEvent) -> list[str]:
        cmds: list[str] = []
        for tc in event.tool_calls or ():
            # Only Bash tool calls carry a shell command to loop on.
            if getattr(tc, "name", "") not in ("Bash", "bash"):
                continue
            inp = getattr(tc, "input", None) or {}
            if not isinstance(inp, dict):
                continue
            raw = inp.get("command") or inp.get("cmd") or ""
            if not isinstance(raw, str):
                continue
            sig = _normalize_cmd(raw)
            if len(sig) >= self.min_cmd_chars:
                cmds.append(sig)
        return cmds

    async def on_after_model(self, event: ModelResponseEvent):
        for sig in self._extract_commands(event):
            self._recent.append(sig)
            count = sum(1 for c in self._recent if c == sig)
            if count >= self.repeat_threshold:
                self._pending_nudge = (
                    _NUDGE_REPEAT
                    if count >= self.escalate_threshold
                    else _NUDGE_FIRST
                )
                # Drop prior occurrences of this command so we nudge once per
                # detected streak and re-arm only if the loop truly continues.
                self._recent = deque(
                    (c for c in self._recent if c != sig), maxlen=self.window
                )
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_nudge:
            yield event
            return
        nudge = self._pending_nudge
        self._pending_nudge = ""
        msgs = list(event.messages)
        # Preserve the +1 message contract: if the run loop already appended a
        # trailing user message, replace it rather than adding a second one.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            msgs[-1] = Message(role="user", content=nudge)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=nudge),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._recent = deque(maxlen=self.window)
        self._pending_nudge = ""
        yield event
