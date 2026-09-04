# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ModuleShadowRecoveryProcessor.

Detect the Python "script name shadows a stdlib/third-party module" trap in
Bash tool output and inject a general recovery hint so the agent does not
mis-conclude that a required output path is "impossible" and abandon it.

Failure class this closes
-------------------------
When a task requires a script at a fixed path whose basename collides with an
importable module (e.g. ``operator.py``, ``queue.py``, ``types.py``,
``email.py``, ``test.py``, ``select.py``, ``code.py`` ...), running it as
``python3 /path/to/name.py`` prepends the script's own directory to
``sys.path[0]``. Any transitive ``import <name>`` then resolves to the script
itself, producing a circular-import / partially-initialized-module crash:

    AttributeError: partially initialized module 'functools' has no attribute
    'lru_cache' (most likely due to a circular import)

Agents frequently misread this as a hard requirement conflict and either
rename the file away from the required path (hard verifier failure) or loop
until they run out of budget. The runtime signature is highly diagnostic and
language-level — not task knowledge — so a mechanical guard can catch it and
redirect the agent toward standard, path-preserving workarounds.

This is intentionally general: it keys off the Python error text, injects no
task-specific paths/constants, and fires at most a small bounded number of
times per task so it never becomes a loop of its own.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import ToolResultEvent
from harnessx.core.processor import MultiHookProcessor

# The two mutually-reinforcing signatures of a script-directory shadow.
#   1. CPython's own words for a shadow-induced circular import.
#   2. A traceback frame that re-enters the *user's* script during an
#      ordinary stdlib import chain (the file appears twice in one trace).
_CIRCULAR_RE = re.compile(
    r"partially initialized module .* has no attribute .* "
    r"most likely due to a circular import",
    re.IGNORECASE | re.DOTALL,
)
_SHADOW_IMPORT_RE = re.compile(
    r"ImportError: cannot import name .* from partially initialized module",
    re.IGNORECASE,
)

_HINT = (
    "\n\n[ModuleShadowGuard] This looks like a Python module-shadowing trap, "
    "NOT an impossible requirement. When you run `python3 /some/dir/name.py`, "
    "Python prepends that script's directory to `sys.path[0]`, so a file "
    "named like an importable module (e.g. a stdlib name) shadows the real "
    "module and breaks the stdlib import chain (the tell-tale is a "
    "'partially initialized module ... circular import' error). "
    "Do NOT rename or move the file away from the path the task requires. "
    "Instead keep the file where it must live and change HOW you run it, e.g.:\n"
    "  - `python3 -P /path/to/script.py`  (Py>=3.11) or `python3 -I /path/to/script.py` "
    "to stop the script dir being auto-added to sys.path, or\n"
    "  - run from another directory with `PYTHONPATH` unset and an absolute "
    "target, e.g. `cd /tmp && python3 /path/to/script.py`, or\n"
    "  - inside the script, remove the offending entry early: "
    "`import sys; sys.path[:] = [p for p in sys.path if p not in ('', '/its/dir')]` "
    "before the conflicting import.\n"
    "Verify the required file still exists at its exact required path after you "
    "get it running."
)


class ModuleShadowRecoveryProcessor(MultiHookProcessor):
    """Inject a general recovery hint on Python module-shadow crashes.

    Fires on Bash tool results whose output carries the diagnostic
    circular-import / partially-initialized-module signature. Bounded to a
    small number of injections per task so it cannot itself become a loop.
    """

    _singleton_group = "module_shadow_guard"
    _order = 31  # after CustomEditToolProcessor (30), still an on_after_tool hook

    def __init__(self, max_hints: int = 3) -> None:
        self.max_hints = max_hints
        self._fired = 0

    async def on_task_start(self, event):
        self._fired = 0
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if self._fired >= self.max_hints:
            yield event
            return
        result = event.result or ""
        if _CIRCULAR_RE.search(result) or _SHADOW_IMPORT_RE.search(result):
            self._fired += 1
            yield dataclasses.replace(event, result=result + _HINT)
        else:
            yield event

    async def on_task_end(self, event):
        self._fired = 0
        yield event
