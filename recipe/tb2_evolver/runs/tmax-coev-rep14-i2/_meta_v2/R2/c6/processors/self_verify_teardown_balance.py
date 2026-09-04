"""SelfVerifyTeardownBalance — de-bias the one-sided self-verify checklist.

The stock TB2 self-verify checklist (CustomSelfVerifyProcessor, injected once
per task right before the agent exits) contains a single service-lifecycle
item that is one-sided:

    5. **For running services:** confirm they are still alive and reachable
       right now, not just that they started earlier.

That steering is correct for tasks whose verifier connects to a service the
agent must leave running, but it is *actively wrong* for lifecycle / init-
script / CI-CD-pipeline tasks whose verifier asserts a CLEAN TEARDOWN — no
lingering processes at exit. On those tasks the agent starts a service (its
own testing, or by running the pipeline it authored) and, nudged only toward
"keep it alive", never checks for or reaps stray processes before exiting.

This processor makes that checkpoint two-sided. It hooks ``on_before_model``
*after* CustomSelfVerifyProcessor (``_order`` 91 > 90) so that when the
self-verify checklist has just been appended as the last user message, it
augments that same message with a balanced teardown item. It does NOT insert
a new message — it edits the content of the last user message only, which is
the sole message-mutation the ``on_before_model`` contract permits when the
last message is a user message.

Generality: the added guidance is a strategy ("decide whether the verifier
expects the service alive or torn down, and match that end state"), not a
task-specific recipe. No task ids, ports, process names, or file paths appear
here. It fires at most once per task (gated on the self-verify sentinel, which
CustomSelfVerify injects exactly once), and only when that sentinel is present
— so tasks that never reach the self-verify checkpoint are untouched, and the
edit is idempotent (guarded by a second sentinel it inserts).
"""

from __future__ import annotations

import dataclasses

from harnessx.core.events import BeforeModelEvent, Message
from harnessx.core.processor import MultiHookProcessor


# Substring that uniquely identifies the stock self-verify checklist message.
# Matches CustomSelfVerifyProcessor._SELF_VERIFY_MSG in
# benchmarks/terminal_bench_2/harness.py. Kept as a stable phrase, not a task
# literal.
_SELF_VERIFY_SENTINEL = "run through this checklist"

# Marker so we never double-append if some future pipeline re-emits the event.
_TEARDOWN_MARKER = "[lifecycle end-state check]"

_TEARDOWN_ITEM = (
    "\n\n"
    + _TEARDOWN_MARKER
    + "\n**Match the required end state for any process you started.** "
    "Item 5 above only covers services the verifier will connect to. If the "
    "task is a lifecycle / init-script / deploy-or-CI pipeline whose success "
    "criterion is a clean shutdown (e.g. 'gracefully stop', 'no lingering "
    "processes', 'terminate on exit'), then any process you launched — "
    "including during your own testing or by running a pipeline you wrote — "
    "must be fully stopped before you finish. Decide which end state the task "
    "wants:\n"
    "  - Service must stay up  -> confirm it is still listening (item 5).\n"
    "  - Service must be torn down -> stop it AND verify none survive "
    "(e.g. list matching processes and confirm the list is empty; if a stop "
    "signal was sent, wait briefly and re-check, escalating if needed).\n"
    "Do not leave a process running that the task asked you to shut down, and "
    "do not kill one the verifier still needs."
)


class SelfVerifyTeardownBalance(MultiHookProcessor):
    """Append a two-sided service end-state item to the self-verify checklist."""

    _singleton_group = "tb2_self_verify_teardown_balance"
    _order = 91  # after CustomSelfVerifyProcessor (_order=90)

    def __init__(self) -> None:
        self._applied = False

    async def on_task_start(self, event):
        self._applied = False
        yield event

    async def on_task_end(self, event):
        self._applied = False
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        msgs = event.messages
        if self._applied or not msgs:
            yield event
            return

        last = msgs[-1]
        if last.role != "user":
            yield event
            return

        content = last.content
        # Only augment plain-text self-verify checklist messages.
        if not isinstance(content, str) or _SELF_VERIFY_SENTINEL not in content:
            yield event
            return
        if _TEARDOWN_MARKER in content:
            self._applied = True
            yield event
            return

        new_last = dataclasses.replace(last, content=content + _TEARDOWN_ITEM)
        self._applied = True
        yield dataclasses.replace(event, messages=msgs[:-1] + (new_last,))
