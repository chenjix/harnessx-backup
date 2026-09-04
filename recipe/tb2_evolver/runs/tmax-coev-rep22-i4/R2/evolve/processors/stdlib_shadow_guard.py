# SPDX-License-Identifier: MIT
"""StdlibShadowGuard — recover from Python stdlib-module name shadowing.

Failure mode this closes
------------------------
Some tasks require the agent to author a Python file whose *basename* collides
with a Python standard-library module — e.g. a task that says "write a script
at ``/home/user/operator.py``" (``operator`` is a stdlib module), or ``types.py``,
``queue.py``, ``string.py``, ``select.py``, ``code.py``, ``token.py``,
``calendar.py``, ``test.py`` etc.

When that file lives in a directory that ends up on Python's ``sys.path``
(the script's own directory, or the current working directory), *every*
subsequent ``python3`` invocation in that directory is poisoned: Python's own
interpreter start-up imports ``collections`` -> ``from operator import eq``,
which now resolves to the agent's file instead of the real stdlib module,
producing a circular import:

    ImportError: cannot import name 'namedtuple' from partially initialized
    module 'collections' (most likely due to a circular import)
    (/usr/lib/python3.10/collections/__init__.py)
    ...
    File "/home/user/operator.py", line N, in <module>
    Could not import runpy module

This breaks the *external verifier phase* too: the grader runs pytest / runpy
from that same directory, so the collision crashes the grader before it can
test anything — a fully-correct solution still scores 0. Worse, the agent
usually *observes* the breakage during its own session (its own
``python3 -c 'import <lib>'`` fails with this exact traceback) but does not
know the escape, because the task explicitly demands that filename.

Observed on the evolve set
--------------------------
* ``task_000010_644ab1c2`` (system_administration): task required a script at
  ``/home/user/operator.py``. The agent wrote it (with module-level
  ``import subprocess`` etc.), its own ``python3 -c 'import pexpect'`` broke
  with the circular-import traceback, it renamed / removed the file to test,
  restored it to ``operator.py`` at the end, and exited. The verifier's
  ``final_pytest`` died with ``Could not import runpy module`` /
  ``partially initialized module 'collections' ... circular import`` pointing
  at ``/home/user/operator.py`` — reward 0 despite the backup + api log being
  correct.

Why the existing pipeline misses it
-----------------------------------
No processor inspects tool *output* for the stdlib-shadow signature. The
``CyclicLoopBreaker`` only keys on identical repeated output; here the agent's
repeated ``import`` attempts return the same failure but the agent already
"knows" what is wrong — it just lacks the escape strategy. The
``HttpVerifierDep*`` processors handle a *missing* dependency, not a *shadowed
stdlib* one. This is a capability-gap the agent surfaces in a tool result but
has no reasonable way to act on.

Design — detect in tool output, inject the escape strategy once
---------------------------------------------------------------
``on_after_tool``: when a ``Bash`` result carries the structural circular-import
/ "partially initialized module" signature AND the traceback frame names a
``*.py`` file whose basename (minus extension) is a known Python stdlib module,
append a one-time guidance block to that tool result. The guidance explains the
mechanism and gives *general strategies* to make the required-name file
harmless:

  1. Guard every top-level import / side effect behind
     ``if __name__ == '__main__':`` so importing the file is a cheap no-op...
  2. ...but because Python may still need real attributes from the shadowed
     module, the robust fix is to make the file a **transparent shim** that
     re-exports the genuine stdlib module (load it from the real stdlib path
     with ``importlib``), then put the task logic under ``__main__``; OR
  3. keep the deliverable at the required path but run / test it from a
     directory that is NOT on ``sys.path`` for that name (e.g. invoke it by
     absolute path from ``/`` so the shadow dir is not ``sys.path[0]``),
     verifying that a bare ``python3 -c 'import <stdlibname>'`` from the
     grader's likely CWD still works.

The processor carries **no task-specific constants** — it keys purely on the
structural circular-import signature plus a static list of Python stdlib module
names, and names the offending file/module it extracted from the traceback so
the guidance is actionable. Fires at most once per task.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sys

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Structural signature of a stdlib-shadow-induced circular import. We require a
# circular-import / partially-initialized signal to avoid firing on ordinary
# ImportErrors (e.g. a genuinely missing third-party package).
_CIRCULAR_SIG = re.compile(
    r"(?is)"
    r"(?:partially\s+initialized\s+module"
    r"|most\s+likely\s+due\s+to\s+a\s+circular\s+import"
    r"|Could\s+not\s+import\s+runpy\s+module)"
)

# Traceback frames pointing at a .py file the agent likely authored. We look at
# every referenced file path and test its basename against the stdlib set.
_FILE_FRAME = re.compile(r'File\s+"([^"]+\.py)"')


def _stdlib_module_names() -> frozenset[str]:
    """Names that Python treats as standard-library modules on this interpreter.

    Uses ``sys.stdlib_module_names`` (3.10+) when available, plus a small
    always-shadow-prone fallback set so the guard still works if that attribute
    is missing. Only *top-level* names matter for filename shadowing.
    """
    names: set[str] = set()
    stdlib = getattr(sys, "stdlib_module_names", None)
    if stdlib:
        names.update(stdlib)
    else:
        names.update(sys.builtin_module_names)
    # Common single-file stdlib modules that recur as tempting task filenames.
    names.update(
        {
            "operator", "types", "string", "queue", "select", "token", "code",
            "calendar", "platform", "signal", "socket", "struct", "array",
            "copy", "enum", "json", "math", "random", "re", "time", "test",
            "email", "http", "abc", "io", "os", "sys", "csv", "gzip", "glob",
            "uuid", "hashlib", "logging", "argparse", "collections", "typing",
            "secrets", "statistics", "decimal", "fractions", "numbers",
        }
    )
    # Do not treat pseudo private / dunder names as shadow candidates.
    return frozenset(n for n in names if n and not n.startswith("_"))


_STDLIB = _stdlib_module_names()


def _guidance(module: str, path: str) -> str:
    return (
        "\n\n[StdlibShadowGuard] CRITICAL — Python stdlib name collision "
        f"detected. The file `{path}` shadows the Python standard-library "
        f"module `{module}`. Because that file's directory is on Python's "
        "import path, EVERY `python3` invocation from that directory now "
        "crashes during interpreter start-up (the circular import you just "
        "saw), INCLUDING the automated grader that runs after you finish — so "
        "even a fully-correct solution will score 0 while this file exists "
        "under this name.\n"
        "You may be required to keep the file at this exact path. To satisfy "
        "that requirement WITHOUT poisoning the interpreter, use one of these "
        "general strategies and then re-verify:\n"
        f"  1. Make `{path}` a *transparent shim*: at the very top, load the "
        f"real stdlib `{module}` from its genuine location (e.g. via "
        "`importlib` using a path that excludes this directory) and re-export "
        "its public attributes, so anything importing "
        f"`{module}` still works. Put your task logic under "
        "`if __name__ == '__main__':` so it only runs when executed directly.\n"
        "  2. If nothing needs to import your file as a module, still guard "
        "ALL top-level imports and side effects behind "
        "`if __name__ == '__main__':` — but verify that a bare "
        f"`python3 -c 'import {module}'` from the grader's likely working "
        "directory (often the file's directory or `/home/user`) now succeeds; "
        "if it still fails, strategy 1 is required.\n"
        "  3. Confirm the fix END-TO-END before finishing: run "
        f"`cd <that_dir> && python3 -c 'import {module}; import collections; "
        "print(\"ok\")'` and make sure it prints ok, then re-run your actual "
        "deliverable so its side effects (files created, logs written) are "
        "regenerated in the now-clean interpreter."
    )


class StdlibShadowGuard(MultiHookProcessor):
    """Detect stdlib-name shadowing in Bash output; inject escape strategy once."""

    _singleton_group = "stdlib_shadow_guard"
    # After CyclicLoopBreaker (22) so a repeated-import loop warning still fires,
    # and well before the self-verify / exit processors so the agent gets the
    # guidance mid-session with budget left to act on it.
    _order = 25

    def __init__(self) -> None:
        self._fired = False

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if self._fired or event.tool_name != "Bash":
            yield event
            return

        blob = (event.result or "")
        if event.error:
            blob += "\n" + str(event.error)
        if not blob or not _CIRCULAR_SIG.search(blob):
            yield event
            return

        # Find a traceback frame whose file basename shadows a stdlib module.
        offender_path = None
        offender_mod = None
        for m in _FILE_FRAME.finditer(blob):
            path = m.group(1)
            base = os.path.basename(path)
            stem, ext = os.path.splitext(base)
            if ext != ".py":
                continue
            # Ignore frames inside the interpreter's own stdlib install; only
            # user-authored files (outside site-packages / lib/pythonX) can be
            # the shadowing culprit.
            low = path.replace("\\", "/").lower()
            if "/lib/python" in low or "site-packages" in low or "dist-packages" in low:
                continue
            if stem in _STDLIB:
                offender_path = path
                offender_mod = stem
                break

        if offender_mod is None:
            yield event
            return

        self._fired = True
        yield dataclasses.replace(
            event,
            result=(event.result or "") + _guidance(offender_mod, offender_path),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        yield event
