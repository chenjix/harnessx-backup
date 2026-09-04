# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StdlibShadowGuard — diagnose self-inflicted Python interpreter breakage.

Closes a systemic, self-inflicted failure mode that a weak model cannot
diagnose on its own: the agent writes a Python script whose *filename*
collides with a standard-library module name (``operator.py``, ``socket.py``,
``queue.py``, ``types.py``, ``select.py``, ``code.py``, ``string.py``,
``json.py``, ``random.py``, ``copy.py``, ...), then runs ``python3`` with
that script's directory as the current working directory. Because CPython
puts the script's directory first on ``sys.path``, the user file *shadows*
the stdlib module of the same name. The very first stdlib import that
transitively pulls in the shadowed module then fails with a confusing
partially-initialized / circular-import traceback whose stack contains a
frame in the user's own file.

Observed shape (task_000010_644ab1c2): the agent named its script
``/home/user/operator.py`` (mandated by the task), ran
``python3 /home/user/operator.py`` from ``/home/user``. Python's
``collections/__init__.py`` does ``from operator import eq as _eq``, which
resolved to the user's script → ``ImportError: cannot import name
'namedtuple' from partially initialized module 'collections' ... circular
import``. This broke *every* subsequent ``python3`` invocation run from that
directory. The model misdiagnosed it as "a Python 3.10 compatibility issue"
and re-ran variants of the same broken command 16+ times until the loop
guard terminated the episode — never realizing its own filename was the
cause. No amount of rewriting the script's imports can fix this; the fix is
to run Python from a neutral working directory (e.g. ``cd /tmp && python3
/path/to/script.py``) or set ``PYTHONSAFEPATH=1`` / clear ``sys.path[0]``.

This processor is a Control-lever, warn-only ``on_after_tool`` hook: when a
Bash result carries the unmistakable stdlib-shadow signature (a
partially-initialized-module / circular-import ImportError whose traceback
references a user-writable path, i.e. NOT under a system library dir), it
appends a targeted diagnostic to the tool result explaining the root cause
and the concrete recovery. It never blocks, never terminates, and never
fires on ordinary import errors — so it cannot regress a passing task.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import TaskStartEvent, ToolResultEvent
from harnessx.core.processor import MultiHookProcessor

# The circular-import / partially-initialized signature CPython emits when a
# core stdlib module is shadowed during its own initialization.
_SIG_PARTIAL = re.compile(
    r"partially initialized module|circular import|"
    r"cannot import name .+ from partially initialized",
    re.IGNORECASE,
)

# A traceback frame pointing at a user-writable location (NOT a system stdlib
# dir). If the confusing import error's stack includes such a frame, the user's
# own file is almost certainly the shadowing culprit.
_USER_FRAME = re.compile(r'File "(/(?!usr/|opt/python|Library/|System/)[^"]+\.py)"')

# System library directories — frames here are the stdlib itself, not the shim.
_SYSLIB = re.compile(r"/usr/lib|/usr/local/lib|site-packages|dist-packages")

_DIAGNOSTIC = (
    "\n\n[StdlibShadowGuard] ROOT CAUSE DETECTED — this is NOT a Python "
    "version or compatibility bug. The traceback shows a *standard library* "
    "import failing while it initializes, with a frame pointing at YOUR OWN "
    "script ({user_file}). This happens when a Python file you created has "
    "the SAME NAME as a standard-library module (e.g. operator.py, socket.py, "
    "queue.py, types.py, select.py, string.py, json.py, random.py, code.py) "
    "AND you run `python3` from the directory that contains it — Python puts "
    "that directory first on sys.path, so your file shadows the real stdlib "
    "module and breaks nearly every import.\n"
    "Rewriting the script's own imports will NOT fix this. Recover with ONE of:\n"
    "  1. Run from a neutral directory so the script dir is not sys.path[0]:\n"
    "       cd /tmp && python3 {user_file}\n"
    "  2. Force safe path resolution:\n"
    "       PYTHONSAFEPATH=1 python3 {user_file}\n"
    "  3. If the filename is not mandated by the task, rename the file to "
    "something that does not collide with a stdlib module.\n"
    "Do not keep re-running the same command — apply one of the fixes above."
)


class StdlibShadowGuard(MultiHookProcessor):
    """Detect stdlib-module shadowing and inject a one-time recovery diagnostic."""

    _singleton_group = "stdlib_shadow_guard"
    _order = 45

    def __init__(self, max_injections: int = 3) -> None:
        # Cap how many times we append the diagnostic per task so a persistent
        # loop does not spam context; the loop guard handles termination.
        self.max_injections = max(1, int(max_injections))
        self._injections = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._injections = 0
        yield event

    def _detect_user_file(self, text: str) -> str | None:
        if not text or not _SIG_PARTIAL.search(text):
            return None
        # Find a traceback frame that is a user-writable .py file (not syslib).
        for m in _USER_FRAME.finditer(text):
            path = m.group(1)
            if not _SYSLIB.search(path):
                return path
        return None

    async def on_after_tool(self, event: ToolResultEvent):
        if self._injections >= self.max_injections:
            yield event
            return
        blob = (event.result or "") + ("\n" + event.error if event.error else "")
        user_file = self._detect_user_file(blob)
        if user_file is None:
            yield event
            return
        self._injections += 1
        note = _DIAGNOSTIC.format(user_file=user_file)
        yield dataclasses.replace(event, result=(event.result or "") + note)
