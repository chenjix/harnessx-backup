# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""UnmetRequirementExitGuard — block a self-sabotaging exit.

Motivating failure shape (TB2, `no_tool_calls` exits): the agent builds a
working solution, then talks itself into shipping a *knowingly incomplete*
deliverable — most commonly leaving a required output at the wrong path or in
the wrong form because it hit a secondary technical wall (e.g. a filename that
shadows a stdlib module, so the script "can't" be named as required). The
agent verbalises the unmet requirement ("The only issue is that the script
can't be named operator.py ...") and then exits anyway, scoring 0 on a
final-state existence check.

The existing ``CustomSelfVerifyProcessor`` fires a generic checklist on the
*first* exit regardless of content, so once that one-shot has been spent the
agent is free to exit with an acknowledged blocker. This processor is
orthogonal: it fires (at most once per task) only when the exit turn's text
*admits* an unresolved required-deliverable conflict. In that case it injects
one focused nudge telling the agent that a required deliverable must be
satisfied at its exact specified path/form — the secondary obstacle must be
worked around, not used as a reason to abandon the requirement.

Design constraints (mirrors CustomSelfVerifyProcessor):
- one-shot per task (never nags a clean exit, never loops);
- keeps the +1-user-message contract (append exactly one user message on the
  next ``on_before_model`` after a tool result);
- content-gated: only fires when acknowledged-blocker phrasing is present, so
  exits that don't admit a problem pass straight through.
"""

from __future__ import annotations

import dataclasses
import re
import uuid

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
    ToolCall,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

_GUARD_TOOL = "_tb2_unmet_requirement_guard"
_GUARD_ACK = "Requirement-reconciliation check initiated. See the message above."

# Phrases that signal the agent is exiting while consciously leaving a required
# deliverable unmet or in the wrong place/form. Kept deliberately general so it
# generalises across task domains, not tuned to any one task's wording.
_ACK_BLOCKER_RE = re.compile(
    r"(?:"
    r"the only (?:issue|problem|remaining)"
    r"|only (?:issue|problem) is"
    r"|however[, ].{0,60}(?:task|require|need|expect|must)"
    r"|but the task (?:require|specif|expect|want|say)"
    r"|task (?:specifically |explicitly )?(?:require|specif|expect|want|say)"
    r"|(?:can'?t|cannot|couldn'?t|could not|unable to|was unable|failed to) "
    r"(?:be named|name|create|produce|write|place|put|generate|run it at|satisfy)"
    r"|(?:should|must|needs? to|has to) be (?:at|named|located|called|placed)"
    r"|(?:instead of|rather than) (?:the )?(?:required|requested|expected|specified)"
    r"|(?:renamed|moved|placed) .{0,40}(?:instead|because|since|as a workaround)"
    r"|does not (?:exist|match|meet)|doesn'?t (?:exist|match|meet)"
    r"|not (?:at|in) the (?:required|requested|expected|specified|exact) (?:path|location|name)"
    r"|left (?:it |the file |the output )?(?:at|as|in)"
    r")",
    re.IGNORECASE,
)

_NUDGE = """\
Before you exit: your last message acknowledges a requirement you have NOT
fully satisfied (a required output left at the wrong path/name/form, or a
requirement you decided you "couldn't" meet). A secondary obstacle is NOT a
license to ship an incomplete deliverable — the final-state check grades the
EXACT deliverable the task specified, and a correct solution placed at the
wrong path scores zero.

Do this now, then continue:

1. Re-read the task and list every required output with its EXACT path/name
   and form. Write it down.
2. For each requirement you deprioritised because of a secondary problem:
   satisfy the requirement AND work around the obstacle. They are almost never
   mutually exclusive. Examples of workarounds instead of abandoning a path:
   - A required script filename that shadows a module only breaks when it is
     *imported/executed from its own directory*; you can still place the file
     at the required path and run/verify it in a way that avoids the shadow
     (run from a different working directory, invoke via an absolute path from
     elsewhere, or run a copy under a safe name while the required file stays
     put).
   - A "file must be here but my tool wrote it there" conflict is solved by
     copying/moving the artifact to the required path, not by changing the
     requirement.
3. Confirm with `ls -lh` (and, where relevant, contents) that the deliverable
   now exists at the EXACT required path/name.

Fix it, verify, then finish. If — after genuinely attempting the workaround —
a requirement is truly impossible in this environment, state precisely why and
what you tried.\
"""


class UnmetRequirementExitGuard(MultiHookProcessor):
    """One-shot: intercept an exit that admits an unmet required deliverable."""

    _singleton_group = "tb2_unmet_requirement_guard"
    _order = 91  # just after CustomSelfVerifyProcessor (_order=90)

    def __init__(self) -> None:
        self._fired = False
        self._pending_message: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._pending_message = ""
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        text = event.content or ""
        if exit_intent and not self._fired and _ACK_BLOCKER_RE.search(text):
            self._fired = True
            self._pending_message = _NUDGE
            keepalive = ToolCall(
                id=f"urg-{uuid.uuid4().hex[:8]}",
                name=_GUARD_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _GUARD_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_GUARD_ACK
            )
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._pending_message = ""
        yield event
