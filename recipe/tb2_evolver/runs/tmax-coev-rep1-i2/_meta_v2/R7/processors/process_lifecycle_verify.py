# SPDX-License-Identifier: MIT
"""Exit-time self-verification augmented with process-lifecycle reconciliation.

Extends the stock TB2 ``CustomSelfVerifyProcessor`` one-shot exit checklist
with a *process/service lifecycle* reconciliation step. The stock checklist
covers output files and "services still alive", but has no coverage for two
recurring, verifier-checked failure shapes on process-management tasks:

  1. Processes that the task requires to be **stopped/cleaned up** are left
     lingering after the agent's own testing spawned them (the verifier greps
     for them and fails on any survivor).
  2. A recorded PID (e.g. a ``*.pid`` file) points at a shell wrapper rather
     than the target binary, so ``/proc/<pid>/comm`` reads ``bash`` / ``sh``
     instead of the program name the verifier expects.

Both are general shell-lifecycle disciplines, not task-specific knowledge.
The augmented checklist prompts the agent to reconcile the final process
table against the task's stated lifecycle requirements (which processes must
persist, which must be gone) and to confirm any recorded PID names the actual
program. Everything mechanical (one-shot keepalive, singleton group, before
model injection) is inherited unchanged from the stock processor, so this adds
no new mechanical regression surface.
"""

from __future__ import annotations

import dataclasses
import uuid

from harnessx.core.events import ModelResponseEvent, ToolCall

from benchmarks.terminal_bench_2.harness import (
    CustomSelfVerifyProcessor,
    _SELF_VERIFY_MSG,
    _SELF_VERIFY_TOOL,
)


# The stock checklist plus a general process-lifecycle reconciliation step.
# No task ids, paths, ports, or process names from any training task appear
# here — it describes the *class* of check, letting the agent fill in the
# concrete requirements from the task description it re-reads in step 1.
_LIFECYCLE_CLAUSE = """
6. **Reconcile the final process/service state with the task's lifecycle requirements.** \
Run `ps -ef` (or `pgrep -a`) and read the task description again for lifecycle wording:
   - Every process/service the task requires to keep **running** must still be alive right now.
   - Every process the task requires to be **stopped, cleaned up, or not left lingering** \
(including any you spawned only for your own testing) must be gone — kill leftovers before exiting.
   - If the task asks you to record a PID (e.g. a `*.pid` file), confirm that PID names the \
**actual target program**, not a shell wrapper: check `cat /proc/<pid>/comm`. Launching via \
`bash -lc "... &"` or `nohup sh -c "prog &"` records the wrapper's PID, not `prog`'s — start \
the binary directly (or `exec` it) so the recorded PID is the program itself.
"""


class ProcessLifecycleSelfVerifyProcessor(CustomSelfVerifyProcessor):
    """CustomSelfVerifyProcessor with an appended process-lifecycle checklist item.

    Reuses the stock singleton group ``tb2_self_verify`` so it *replaces* the
    stock processor in the pipeline rather than firing a second, conflicting
    keepalive. Only ``on_after_model`` is overridden — to arm the extended
    message; ``on_before_model`` / ``on_before_tool`` / task lifecycle hooks
    are inherited verbatim.
    """

    def __init__(self) -> None:
        super().__init__()
        # Splice the lifecycle clause in before the closing "Fix anything..."
        # instruction so the SUCCESS marker guidance stays last.
        marker = "\nFix anything that looks wrong before exiting."
        if marker in _SELF_VERIFY_MSG:
            head, tail = _SELF_VERIFY_MSG.split(marker, 1)
            self._message = head + _LIFECYCLE_CLAUSE + marker + tail
        else:
            self._message = _SELF_VERIFY_MSG + "\n" + _LIFECYCLE_CLAUSE

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            self._verified = True
            self._pending_message = self._message
            keepalive = ToolCall(
                id=f"sv-{uuid.uuid4().hex[:8]}",
                name=_SELF_VERIFY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event
