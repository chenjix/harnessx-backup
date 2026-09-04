# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""StdlibShadowDiagnosticProcessor for Tmax (and TB2-style) agents.

Closes a runtime-awareness gap: when a task requires the agent to create a
Python file whose *basename collides with a Python standard-library module*
(e.g. a file the task names ``operator.py``, ``token.py``, ``types.py``,
``queue.py``, ``select.py``, ...), running ``python3 <that file>`` — or later
running pytest — from the file's own directory makes CPython prepend that
directory to ``sys.path[0]``. Any stdlib import chain that transitively
imports the shadowed module then resolves to the *user's* file instead of the
real stdlib module, producing a fatal, self-referential traceback:

    from operator import eq as _eq
    File ".../operator.py", line 1, in <module>
    ImportError: cannot import name '...' from partially initialized module
    'collections' (most likely due to a circular import)

or, when the user file isn't even valid Python:

    SyntaxError: invalid syntax
    Could not import runpy module

This is a well-known CPython ``sys.path[0]`` footgun that is invisible unless
you already know the mechanic. Observed shape (r0 trajectory
task_000010_644ab1c2): the agent read this traceback as a bug in its *own*
script, thrashed for 60+ steps renaming / symlinking / rewriting the required
file — eventually corrupting it into a non-Python bash wrapper, which is the
exact state that then fails the verifier's pytest. The generic loop-recovery
nudge fired but could not supply the specific insight needed to escape.

This processor:
* scans each completed tool result (result + error text) for the shadowing
  signature — a stdlib import chain redirected into a user file living outside
  the interpreter's own lib directories;
* when detected, injects ONE targeted, generalizable diagnostic before the
  next model call that (a) explains the sys.path[0] mechanic in general terms,
  and (b) lists concrete, task-agnostic escape routes (invoke from a different
  working directory, use ``python3 -P`` / ``PYTHONSAFEPATH=1``, or manipulate
  ``sys.path`` inside the script) WITHOUT renaming the required file.

The nudge names no task, path, filename, or constant that is specific to any
one task — it detects a structural Python footgun and describes the general
remedy, so it applies to any task whose required deliverable shadows a stdlib
module. It fires at most ``max_fires`` times per task to avoid nagging.
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

# A traceback frame that points at a .py file NOT under a Python install /
# site-packages location — i.e. a user file in the working tree.
_FRAME_RE = re.compile(
    r'File "(?P<path>[^"]+\.py)", line \d+', re.MULTILINE
)

# Directory fragments that mark an interpreter-owned (not user) file. A frame
# under any of these is stdlib / packaging, never the shadowing culprit.
_SYSTEM_DIR_MARKERS = (
    "/lib/python",
    "/lib64/python",
    "site-packages",
    "dist-packages",
    "/usr/lib/python",
    "lib/python3",
)

# Signatures that, combined with a user-file frame in the SAME traceback,
# indicate stdlib-module shadowing rather than an ordinary bug in user code.
_SHADOW_SIGNATURES = (
    "partially initialized module",
    "most likely due to a circular import",
    "could not import runpy module",
    "failed to import the site module",
    "init_import_site",
)

# Import lines that only appear when a *stdlib* import chain is being
# redirected (a plain user bug rarely bottoms out in these core modules).
_STDLIB_CHAIN_HINTS = (
    "from collections import",
    "import collections",
    "from operator import",
    "import functools",
    "import importlib",
    "import contextlib",
)

_NUDGE = (
    "RUNTIME DIAGNOSTIC — possible standard-library name shadowing.\n"
    "The traceback you just saw is not a bug in your program logic. It is the "
    "CPython sys.path[0] footgun: when you run `python3 <file>` (or pytest) "
    "from a directory that contains a .py file whose NAME matches a Python "
    "standard-library module, that directory is placed FIRST on sys.path, so "
    "the interpreter imports YOUR file instead of the real stdlib module. Any "
    "stdlib import chain that transitively needs the shadowed module then fails "
    "with 'partially initialized module' / 'circular import' / 'Could not "
    "import runpy module'.\n"
    "Do NOT rename, delete, symlink, or rewrite the required file — if the task "
    "specifies that exact filename, keep it. Instead pick ONE structural fix:\n"
    "  (1) Run the script from a DIFFERENT working directory so its own folder "
    "is not sys.path[0] (e.g. cd elsewhere first, or pass an absolute path from "
    "another cwd).\n"
    "  (2) Isolate sys.path: `python3 -P <file>` or `PYTHONSAFEPATH=1 python3 "
    "<file>` (Python 3.11+ suppresses the script-dir entry), or on older "
    "Python set `PYTHONPATH` / edit `sys.path` at the very top of the script "
    "before any other import.\n"
    "  (3) If the file must be importable as a module by other code, make its "
    "own imports absolute-safe by removing its directory from sys.path[0] at "
    "startup.\n"
    "First verify the collision (compare the file's basename against Python "
    "stdlib module names), then apply the smallest fix that preserves the "
    "required filename and rerun."
)


class StdlibShadowDiagnosticProcessor(MultiHookProcessor):
    """Detect stdlib-module shadowing tracebacks and inject a targeted fix."""

    _singleton_group = "tmax_stdlib_shadow_guard"
    # After repeat-command recovery (7); before compaction (8) so the
    # diagnostic survives into the next model turn.
    _order = 7

    def __init__(self, max_fires: int = 2) -> None:
        # Fire at most this many times per task. The signature can recur while
        # the agent works toward the fix; we warn, then trust it to act.
        self.max_fires = max(1, int(max_fires))
        self._fires: int = 0
        self._pending: bool = False

    def _reset(self) -> None:
        self._fires = 0
        self._pending = False

    @staticmethod
    def _is_system_frame(path: str) -> bool:
        return any(m in path for m in _SYSTEM_DIR_MARKERS)

    def _looks_like_shadow(self, text: str) -> bool:
        if not text:
            return False
        low = text.lower()

        has_signature = any(sig in low for sig in _SHADOW_SIGNATURES)
        has_chain = any(hint in low for hint in _STDLIB_CHAIN_HINTS)

        # Find whether any traceback frame points at a NON-system .py file
        # (i.e. a user file in the working tree) — the shadowing culprit.
        user_frame = False
        for m in _FRAME_RE.finditer(text):
            path = m.group("path")
            if not self._is_system_frame(path):
                user_frame = True
                break

        # Two independent ways to be confident it's shadowing, not a plain bug:
        #  A) an explicit shadow signature (partial-init / runpy / site import)
        #     AND a user-file frame inside a stdlib import chain, OR
        #  B) a stdlib import chain that bottoms out in a user-file frame
        #     (import redirected into the working tree).
        if has_signature and user_frame:
            return True
        if has_chain and user_frame and (
            "circular import" in low or "partially initialized" in low
        ):
            return True
        return False

    async def on_task_start(self, event: TaskStartEvent):
        self._reset()
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if self._fires >= self.max_fires:
            yield event
            return
        combined = (event.result or "") + "\n" + (event.error or "")
        if self._looks_like_shadow(combined):
            self._pending = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending or self._fires >= self.max_fires:
            yield event
            return
        self._pending = False
        self._fires += 1
        msgs = list(event.messages)
        # Contract: never create two consecutive user messages. If the loop
        # already left a trailing user message, merge onto it.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            prev = msgs[-1].content or ""
            merged = (prev + "\n\n" + _NUDGE) if prev else _NUDGE
            msgs[-1] = Message(role="user", content=merged)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=_NUDGE),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._reset()
        yield event
