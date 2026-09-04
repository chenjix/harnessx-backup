# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Self-verify processor variant: verifier-runtime-deps AND orphan-process hygiene.

Motivation (harness deficiency, not task domain knowledge)
----------------------------------------------------------
TB2 verifiers run pytest against the container's *final state* after the
agent's session ends. Two structural facts about that verifier phase
repeatedly cost otherwise-correct solutions their reward, and neither is
visible to the agent from the task description:

1. **Verifier runtime deps.** For network-service tasks the verifier
   probes the service from Python and does ``import requests`` at module
   import time. The agent tests with ``curl``/``wget`` and never installs
   ``requests``; the verifier then dies at collection with
   ``ModuleNotFoundError: No module named 'requests'``, scoring 0 even
   when the service is correct. (Closed in R1.)

2. **Orphaned / zombie background processes at final state.** Tasks that
   involve building or running a daemon/service push the agent to launch
   it repeatedly during development (``./svc &``, ``nohup ... &``,
   ``python -m http.server &``). Some verifiers assert *no lingering
   service processes* via ``pgrep -f <name>`` — and ``pgrep`` matches
   ``<defunct>`` zombies too.

   R2 added a checklist item telling the agent to "clean up" strays and,
   for zombies, "kill the parent PID". That item **fired but did not flip
   the target task** (task_000140): the trajectory shows the agent's
   background service was launched with ``./vm_service &`` inside a
   supervisor subshell that then exited, so the child was **reparented to
   PID 1 (init)**. After ``SIGTERM`` it became ``[vm_service] <defunct>``
   with ``PPID=1``. Such a zombie CANNOT be reaped by the agent — its
   parent is init, which the agent cannot kill, and ``kill -9`` on a
   zombie is a no-op. Worse, the agent then **re-ran its test pipeline as
   one of its final actions**, spawning a *fresh* orphaned instance that
   became yet another zombie under init — undoing any cleanup.

   The correct, general remediation is therefore not "kill the parent"
   (impossible when the parent is init) but **prevention + ordering**:
   - When you launch a background service to test it, capture its PID and
     reap it *in the same shell* with ``kill``/``SIGTERM`` followed by
     ``wait <PID>`` — a child reaped by its own launching shell never
     becomes a lingering ``<defunct>`` entry.
   - Do NOT let a fresh service launch (or a test script that spawns one)
     be one of your *last* actions; run any end-to-end test pipeline
     early, and make your final action a teardown that confirms
     ``pgrep -f <name>`` returns empty.
   - Zombies already orphaned to init cannot be signalled away; the only
     reliable path is to avoid orphaning them in the first place, which
     the two rules above achieve.

Both facts are general, structural properties of how TB2 verifiers
exercise service/daemon tasks — not task-specific domain knowledge.
Because a processor cannot itself run sandbox commands, the remediation
is delivered as items in the one-shot self-verify checklist.

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

7. **Leave no orphaned/zombie background process at final state.** If at any point you launched a service, daemon, or long-running process in the background (`&`, `nohup`, `disown`, `python -m http.server &`, `./some_binary &`, or a test script that does so internally), the container's final state is checked after you exit and some checkers assert that NO such process is left running (e.g. `pgrep -f <name>`) — and that check MATCHES `<defunct>` zombies too. Key facts and the reliable fix:
- A background process launched inside a subshell/script that then exits is **reparented to PID 1 (init)**. Once it dies it becomes a `<defunct>` zombie with `PPID=1` that you **cannot reap or kill** — `kill`/`kill -9` on a zombie is a no-op, and you cannot kill init. So the fix is *prevention*, not cleanup after the fact.
- **Reap in the same shell.** When you launch a background process to test it, capture its PID and, when done, terminate and reap it in that *same* shell:
```bash
./my_service &
SVC_PID=$!
# ... exercise it ...
kill "$SVC_PID" 2>/dev/null; wait "$SVC_PID" 2>/dev/null
```
A child reaped by its own launching shell via `wait` never lingers as a zombie.
- **Ordering matters.** Do NOT make a fresh service launch (or running a test/CI script that spawns one) one of your *last* actions — that leaves a brand-new orphan behind. Run any end-to-end pipeline test EARLY, then make your final action a teardown check:
```bash
pgrep -f '<name-of-your-service-or-binary>' || echo "clean: no lingering processes"
```
Confirm this prints the clean message before exiting. Kill ordinary (non-zombie, still-owned) strays directly with `kill <PID>` and re-check.
- Do NOT do this if the task explicitly requires a service to stay running for the check — in that case leave exactly the one required instance alive and remove only extra dev copies.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class ServiceVerifyDepsProcessor(MultiHookProcessor):
    """One-shot self-verify prompt (files + services + verifier deps + process hygiene).

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
