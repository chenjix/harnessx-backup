# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""IndependentDerivationVerify — augment the once-per-task self-verify checklist.

Motivation (harness deficiency, not a capability gap):
On deterministic-compute tasks (a value or table that is fully determined by
the task description), the agent frequently produces a plausible-but-wrong
result, then "self-verifies" by re-deriving the *same* number from its own
implementation / mental model — a circular check that rubber-stamps its own
bug. The stock TB2 self-verify checklist (harness.py :: _SELF_VERIFY_MSG) asks
the agent to "validate your verification method" but never tells it to compute
the expected result along an *independent* path directly from the specification
and compare. This processor appends one generic checklist item that closes that
gap, injected at the exact decisive moment (the exit-time self-verify
checkpoint) rather than at task start where it is easily forgotten.

Mechanics: hooks ``on_before_model`` at a higher ``_order`` than
``CustomSelfVerifyProcessor`` (_order=90). When the checklist message has just
been appended (detected via a stable sentinel substring from that message), it
rewrites the content of the existing trailing user message to add one extra
item. Net message count change is 0 (it mutates the last message's content,
never inserts / drops / reorders), so it is contract-safe. Fires at most once
per task.

The added item is a general verification-discipline strategy — no task
identifiers, constants, file paths, or algorithms. It applies to any unseen
task whose expected output is deterministically derivable from the description.
"""
from __future__ import annotations

import dataclasses

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Stable sentinel taken from the stock TB2 self-verify checklist header. Used
# only to recognise that the checklist message is present in context; not a
# task-specific literal.
_SELF_VERIFY_SENTINEL = "Before finishing, run through this checklist"

# Idempotency guard: if this text is already in the message, do not re-append.
_ADDED_MARKER = "[independent-derivation check]"

_EXTRA_ITEM = (
    "\n\n6. **Independently re-derive any computed result before trusting it.** "
    + _ADDED_MARKER
    + " If a required value, count, table, or response is fully determined by "
    "the task description (a deterministic computation, formula, mapping, or "
    "lookup), do NOT accept the number your own program printed as proof it is "
    "correct — that is circular. Instead compute the expected result a SECOND, "
    "independent way straight from the specification: work a small case by hand, "
    "re-implement the core step in a different tool (e.g. a short Python "
    "snippet), or trace the spec's definitions element by element. Then compare "
    "against what your solution produces. If they disagree, your solution is "
    "wrong even if it ran without errors — find and fix the discrepancy "
    "(indexing/off-by-one, axis or row/column order, boundary conditions, "
    "rounding/precision, formula terms) before finishing."
)


class IndependentDerivationVerify(MultiHookProcessor):
    """Append an independent-recomputation item to the self-verify checklist."""

    # Distinct group from CustomSelfVerifyProcessor (tb2_self_verify) so it does
    # not conflict; higher _order so it runs AFTER the checklist is appended.
    _singleton_group = "tb2_independent_derivation_verify"
    _order = 95

    def __init__(self) -> None:
        self._fired = False

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if self._fired or not event.messages:
            yield event
            return

        last = event.messages[-1]
        content = getattr(last, "content", None)
        role = getattr(last, "role", None)

        if (
            role == "user"
            and isinstance(content, str)
            and _SELF_VERIFY_SENTINEL in content
            and _ADDED_MARKER not in content
        ):
            self._fired = True
            new_last = dataclasses.replace(last, content=content + _EXTRA_ITEM)
            yield dataclasses.replace(
                event,
                messages=event.messages[:-1] + (new_last,),
            )
            return

        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        yield event
