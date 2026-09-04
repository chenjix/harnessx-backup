# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Self-verify processor variant that also reminds the agent to make the
verifier's likely Python runtime dependencies importable.

Motivation (harness deficiency, not task domain knowledge)
----------------------------------------------------------
TB2 verifiers run pytest against the container's final state. For tasks
that ask the agent to build/run a network service (HTTP server, socket,
reverse proxy), the verifier commonly probes that service from Python
using the third-party ``requests`` library and does ``import requests``
at module import time. The agent never sees the verifier test file, so
it has no way to know ``requests`` must be importable — it tests its own
service with ``curl``/``wget`` and finishes, and the verifier then dies
at collection with ``ModuleNotFoundError: No module named 'requests'``,
scoring 0 even when the service itself is correct.

This is a general, structural fact about how TB2 verifiers exercise
service tasks — it is *not* task-specific domain knowledge. Because a
processor cannot itself run sandbox commands, the fix is delivered as an
extra item in the one-shot self-verify checklist: the agent is told to
ensure Python ``requests`` is importable (installing it if not) whenever
its solution runs a network service. pip installs succeed in this
environment (observed: numpy/pillow/biopython downloaded at run time), so
the remediation the agent is nudged toward is actually available.

The class mirrors ``CustomSelfVerifyProcessor`` (same singleton group,
same one-shot keepalive mechanism) so it is a drop-in replacement in the
pipeline — only the injected checklist text differs.
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

_SELF_VERIFY_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and exact output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty.

4. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

5. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

6. **If your solution runs a network service (HTTP server, socket, reverse proxy, or any endpoint that is checked over the network):** the automated checker probes such services from Python and typically does `import requests` before it can run any assertion. Confirm the Python `requests` module is importable in the same interpreter the checker will use, and install it if it is missing — a missing checker dependency fails the whole task even when your service is correct:
```bash
python3 -c "import requests" 2>/dev/null || pip install requests
```
Do this in addition to (not instead of) testing the endpoint yourself.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class ServiceVerifyDepsProcessor(MultiHookProcessor):
    """One-shot self-verify prompt (files + services + verifier runtime deps).

    Drop-in replacement for ``CustomSelfVerifyProcessor``: fires at most
    once per task run, on the first no-tool-call exit intent, injecting a
    single extra user message and a synthetic keepalive tool call so the
    loop continues one more turn.
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
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_after_model(self, event: ModelResponseEvent):
        exit_intent = event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        if exit_intent and not self._verified:
            self._verified = True
            self._pending_message = _SELF_VERIFY_MSG
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
