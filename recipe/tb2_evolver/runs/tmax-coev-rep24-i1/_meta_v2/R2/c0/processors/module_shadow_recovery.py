# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ModuleShadowRecoveryProcessor — recover from a stdlib module-shadow crash.

Motivating failure shape (TB2 / tmax, "correct logic, wrong path" class):

A task requires a deliverable at an EXACT path/name (a final-state existence
check greps for `os.path.isfile('/home/user/<name>.py')`). The required name
happens to collide with a Python standard-library module (classic footguns:
``operator.py``, ``test.py``, ``random.py``, ``queue.py``, ``token.py``,
``types.py``, ``email.py``, ``string.py``, ``code.py``, ``select.py``, ...).
When the agent runs ``python3 /home/user/<name>.py`` **from that file's own
directory**, the local file shadows the stdlib module and CPython raises a
circular-import / partially-initialized-module error deep inside stdlib
imports (e.g. ``AttributeError: partially initialized module 'functools' has
no attribute 'lru_cache' (most likely due to a circular import)`` or
``ImportError: cannot import name ... from partially initialized module``).

The agent almost always MISDIAGNOSES this as "the file cannot be named X" and
*renames/moves the deliverable to a safe name* to make its own run succeed —
destroying the required deliverable and failing the existence check with 0
reward, even though its solution logic was correct. The real fix is a
**working-directory** change, not a rename: keep the file at the required path
and invoke it from a directory that does NOT contain the shadowing file
(``cd /tmp && python3 /home/user/<name>.py``), or run a safely-named *copy*
while the required file stays put.

This is a mechanical, cross-task Python footgun with a mechanical fix, so it
belongs in a Control hook, not the system prompt. The processor watches Bash
tool output for the shadow-crash signature and, on the first occurrence per
task, injects one focused recovery hint. It contains no task IDs, paths,
thresholds, or identifiers lifted from any trajectory — the module names it
matches are stdlib names, and the guidance is a general strategy that helps an
agent on any task whose required filename collides with an importable module.

Design constraints:
- one-shot per task (never nags, never loops);
- keeps the +1-user-message contract (append exactly one user message on the
  next ``on_before_model`` after the offending tool result);
- signature-gated: only fires when the crash traceback is present, so ordinary
  Bash output passes straight through with zero overhead.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# The distinctive signature of a stdlib module-shadow / circular-import crash.
# Both variants below are produced by CPython when a user file in the current
# working directory shadows a stdlib module that stdlib itself imports early.
_SHADOW_RE = re.compile(
    r"partially initialized module .* circular import"
    r"|cannot import name .* from partially initialized module"
    r"|most likely due to a circular import",
    re.IGNORECASE | re.DOTALL,
)

# Only treat it as a *shadow* crash (not an ordinary circular import in the
# agent's own multi-module code) when the traceback also walks through the
# Python standard library — the shadow bites via stdlib's own early imports.
_STDLIB_TRACE_RE = re.compile(
    r"/usr/lib/python3|/usr/local/lib/python3|lib/python3\.\d+/",
)

_HINT = """\
The traceback you just hit is a Python module-shadow error, NOT a real bug in
your logic and NOT a reason the file "can't" be named what the task requires.

What happened: you ran `python3 <file>.py` from the directory that contains
`<file>.py`, and that filename collides with a standard-library module of the
same name. CPython puts the script's own directory first on `sys.path`, so
stdlib's early internal imports resolve to YOUR file instead of the real
module, and you get a "partially initialized module ... circular import"
error deep inside the standard library.

Do NOT rename or move the required deliverable to "fix" this — the final
grading check verifies the file EXISTS at its EXACT required path/name, so
renaming it to a safe name scores zero even though your code is correct.

Instead, keep the file exactly where the task requires and work around the
shadow at RUN time:
  - Run it from a directory that does not contain the shadowing file, e.g.
    `cd /tmp && python3 /home/user/<file>.py` (an absolute path still works;
    only the *current directory* being on `sys.path` causes the shadow), or
  - Invoke it as a module/other cwd, or run a safely-named COPY for your own
    testing while the required file stays put at its exact path.

Re-run using a non-shadowing working directory, confirm the pipeline completes
end to end, then verify with `ls -lh` that the deliverable is still present at
its EXACT required path before you finish.\
"""


class ModuleShadowRecoveryProcessor(MultiHookProcessor):
    """One-shot: detect a stdlib module-shadow crash and inject a run-time fix."""

    _singleton_group = "tb2_module_shadow_recovery"
    _order = 92  # after self-verify (90) and the unmet-requirement guard slot (91)

    def __init__(self) -> None:
        self._fired = False
        self._pending_message: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._pending_message = ""
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if not self._fired:
            blob = f"{event.result or ''}\n{event.error or ''}"
            if _SHADOW_RE.search(blob) and _STDLIB_TRACE_RE.search(blob):
                self._fired = True
                self._pending_message = _HINT
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

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._pending_message = ""
        yield event
