# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""SpecComplianceVerifyProcessor — a sharper one-shot exit-time self-check.

Drop-in replacement for the stock ``CustomSelfVerifyProcessor``. It keeps the
same *mechanism* (intercept the first no-tool-call exit intent, inject exactly
one user message via a synthetic keepalive tool call, then stay silent) but
swaps the generic "did you address every requirement" checklist for one that
targets the failure class observed in TB2 trajectories:

    The agent substitutes its *own* interpretation of the task for the task's
    *literal* imperatives, then runs a self-test that confirms the
    interpretation rather than the spec. Classic shapes:
      - task says "append" (``>>``); agent uses truncate (``>``)
      - task says "running multiple times must <property>"; agent never
        actually runs it multiple times and checks the property
      - task forbids a side effect (lingering processes, duplicate lines);
        agent never checks the forbidden state is absent
      - task fixes an exact output format / count; agent eyeballs "looks right"

The checklist below is deliberately *general strategy*: it names no task,
constant, path, or identifier from any trajectory. It forces the agent to
(1) extract each explicit imperative verbatim, (2) reproduce the exact usage
pattern the spec describes, and (3) diff observed behaviour against the literal
wording — not against its own restatement.

The mechanism intentionally mirrors the stock processor so the pipeline's
existing exit-time behaviour is preserved; only the injected text differs.
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
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor

_SELF_VERIFY_TOOL = "_tb2_self_verify"
_SELF_VERIFY_ACK = "Verification check initiated. See the message above for instructions."

_SPEC_VERIFY_MSG = """\
Before finishing, run this spec-compliance check. Do NOT skip a step even if you \
already looked — the most common failure here is confirming your own \
interpretation of the task instead of the task's literal wording.

1. **Re-read the task text and list every explicit instruction verbatim.** For \
each imperative, quote the exact phrase (e.g. the precise verb, the exact count, \
the exact path, the exact output string). Do not paraphrase — paraphrasing is \
where requirements get silently reinterpreted.

2. **Watch for wording your implementation may have overridden with an \
assumption.** In particular:
   - append vs overwrite / truncate (`>>` vs `>`, "add to" vs "replace")
   - "exactly N" counts, exact line counts, exact ordering
   - idempotency / "running multiple times" semantics — this constrains *which*
     files change and which must NOT change on re-run; it is not a licence to
     overwrite everything.
   - any side effect the task forbids (no leftover/lingering processes, no
     duplicate entries, no extra files, no error on re-run)
   If your reading of a word disagrees with the literal instruction, the literal
   instruction wins.

3. **Reproduce the exact usage pattern the task describes, then diff against the \
literal requirement.** If the task says a behaviour must hold "when run multiple \
times", actually run it multiple times and check that behaviour. If it fixes an \
output format or count, capture the real output and compare it character-for-\
character / line-for-line against the quoted requirement — not against "looks \
right".

4. **Confirm every required output exists at its exact path** with `ls -lh`, then \
`cat`/`head` each and verify contents are semantically correct, not merely \
present. A script that exited 0 does not prove the file is correct.

5. **Verify no forbidden residual state remains** (stray background processes, \
duplicated lines, temp files) using a command that actually inspects that state.

Fix anything where observed behaviour disagrees with the literal wording, then \
re-run the relevant check. When every quoted requirement matches observed \
behaviour, end your final message with:
**SUCCESS: task complete. Verified each quoted requirement against observed behaviour.**\
"""


class SpecComplianceVerifyProcessor(MultiHookProcessor):
    """Inject a one-shot spec-compliance checklist on the first exit intent.

    Fires at most once per task run. Preserves the stock self-verify mechanism
    (synthetic keepalive tool call + a single injected user message) so the
    pipeline's exit-time contract is unchanged; only the checklist text is
    sharper. On any subsequent no-tool-call turn it stays silent so the agent
    can actually finish.
    """

    _singleton_group = "tb2_self_verify"
    _order = 90

    def __init__(self) -> None:
        self._verified = False
        self._pending_message: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._verified = False
        self._pending_message = ""
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        # Last message is a tool result (role != user) → append exactly +1 user.
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            self._verified = True
            self._pending_message = _SPEC_VERIFY_MSG
            keepalive = ToolCall(
                id=f"sv-{uuid.uuid4().hex[:8]}",
                name=_SELF_VERIFY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _SELF_VERIFY_TOOL:
            yield dataclasses.replace(event, approved=False, synthetic_result=_SELF_VERIFY_ACK)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._verified = False
        self._pending_message = ""
        yield event
