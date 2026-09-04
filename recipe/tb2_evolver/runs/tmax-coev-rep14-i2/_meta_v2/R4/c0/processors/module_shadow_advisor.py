# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""ModuleShadowAdvisor — a TB2 control-lever MultiHookProcessor.

Problem class this closes
-------------------------
Several TB2 tasks require the agent to create a Python script at an EXACT,
task-mandated path/filename (e.g. ``/home/user/operator.py``). When the required
basename collides with a module already importable on ``sys.path`` — most often
a Python standard-library module (``operator.py``, ``queue.py``, ``types.py``,
``select.py``, ``socket.py``, ``tokenize.py``, ``platform.py``, ``email.py``,
``string.py``, ``code.py``, ``token.py``, ``copy.py``, ``json.py``, …) — running
the script with ``python3 /path/to/script.py`` puts the script's directory at
``sys.path[0]``. The stdlib then imports the AGENT'S file instead of the real
module, producing a confusing circular-import crash:

    ImportError: cannot import name 'deque' from partially initialized module
    'collections' (most likely due to a circular import)

The agent's instinctive fix is to RENAME the file (observed: ``operator.py`` ->
``k8s_operator.py``). That silences the crash but directly violates the task's
hard filename requirement, so the ``os.path.isfile('/home/user/operator.py')``
verifier assertion then fails even though the logic was otherwise correct — a
guaranteed reward=0 for a purely mechanical import-resolution reason the agent
misread.

The correct, general fix is to KEEP the required filename and change how the
script is invoked so the script's own directory is not first on ``sys.path``:

  * run it from a directory that does not contain the shadowing name, e.g.
    ``cd /tmp && python3 /home/user/operator.py`` (or ``(cd /tmp; python3 ...)``);
  * or clear the leading path entry: ``PYTHONSAFEPATH=1 python3 /home/user/operator.py``
    (Python 3.11+) / ``python3 -P /home/user/operator.py`` (3.11+);
  * or, portably on any version, run via a wrapper that does not chdir into the
    script's directory.

What this processor does
------------------------
Purely mechanical, contract-safe, and general (no task-specific literals):

* ``on_before_tool`` — remember which Bash calls invoked a Python script by
  path (``python``/``python3`` ... ``<something>.py``), keyed by tool_call_id.
* ``on_after_tool`` — if that call's result carries the module-shadowing
  signature (a circular-import / partially-initialized-module error whose
  traceback re-enters a user-owned ``*.py`` file THROUGH a standard-library
  frame), append a ONE-TIME advisory that names the real cause (basename shadows
  a stdlib module) and the correct fixes (invoke without the script dir on
  ``sys.path``), explicitly warning against the rename anti-pattern when the
  filename is task-required.

Fires **at most once per task** and **only** on the exact shadowing signature,
so it is invisible to the (large) majority of tasks — zero added cost or
regression surface there. It mutates only the tool-result string (the same shape
as ``CustomEditToolProcessor`` / ``OcrQualityAdvisor``); it never inserts, drops,
or reorders messages.
"""
from __future__ import annotations

import dataclasses
import re

from harnessx.core.processor import MultiHookProcessor

# --- detection --------------------------------------------------------------

# The agent ran a Python script by path: `python3 foo.py`, `python /a/b.py args`.
# (Not `python -c ...`, `python -m mod`, or a bare REPL.)
_PY_SCRIPT_RE = re.compile(
    r"\bpython(?:3(?:\.\d+)?)?\b[^\n|;&]*?(?<![\w./-])((?:[\w./-]+/)?([\w.-]+)\.py)\b"
)

# The crash signature of stdlib-module shadowing / a same-file circular import.
_CIRCULAR_RE = re.compile(
    r"most likely due to a circular import"
    r"|partially initialized module"
    r"|cannot import name .* from partially",
    re.IGNORECASE,
)

# A traceback frame pointing at a *user-owned* python file (i.e. NOT the
# interpreter's own stdlib / site-packages tree).
_FRAME_RE = re.compile(r'File "([^"]+\.py)", line \d+, in ')
_STDLIB_HINT_RE = re.compile(
    r"/usr/lib/python|/usr/local/lib/python|site-packages|dist-packages|lib/python\d"
)


def _looks_like_shadow(result_text: str) -> bool:
    """True when the traceback is the stdlib-shadowing circular-import shape.

    Requires (a) a circular-import / partially-initialized error, AND (b) at
    least one traceback frame that points at a *user-owned* .py file (not the
    interpreter tree) AND (c) at least one frame that DOES point into the
    interpreter tree — i.e. control re-entered a user file through a stdlib
    import. This combination is what distinguishes stdlib shadowing from an
    ordinary intra-project circular import.
    """
    if not _CIRCULAR_RE.search(result_text):
        return False
    frames = _FRAME_RE.findall(result_text)
    if not frames:
        return False
    has_user_frame = any(not _STDLIB_HINT_RE.search(f) for f in frames)
    has_stdlib_frame = any(_STDLIB_HINT_RE.search(f) for f in frames)
    return has_user_frame and has_stdlib_frame


_ADVISORY = (
    "\n\n[ModuleShadowAdvisor] This crash is almost certainly a MODULE-NAME "
    "SHADOWING problem, not a bug in your logic. When you run "
    "`python3 /path/to/yourscript.py`, Python puts that script's directory FIRST "
    "on sys.path, so any stdlib `import`/`from` of a module whose name matches "
    "your script's basename resolves to YOUR file instead of the real module — "
    "which produces exactly this 'partially initialized module / circular import' "
    "error. (Classic collisions: operator, queue, types, select, socket, "
    "tokenize, platform, email, string, code, token, copy, json, csv, io.)\n"
    "Do NOT rename the file if the task requires that exact filename/path — "
    "renaming will make the file-existence check fail even though your code is "
    "correct. Instead keep the required name and invoke it so its own directory "
    "is not on sys.path[0]:\n"
    "  - run from elsewhere: `cd /tmp && python3 /full/path/to/yourscript.py` "
    "(or `(cd /tmp; python3 /full/path/to/yourscript.py <args>)`), or\n"
    "  - clear the leading path entry: `PYTHONSAFEPATH=1 python3 /full/path/to/"
    "yourscript.py` or `python3 -P /full/path/to/yourscript.py` (Python 3.11+).\n"
    "After it runs cleanly, re-confirm the required output file still exists at "
    "its mandated path before finishing."
)


class ModuleShadowAdvisor(MultiHookProcessor):
    """One-time nudge that recognises stdlib-module shadowing and steers away
    from the rename anti-pattern when a filename is task-required."""

    _singleton_group = "module_shadow_advisor"
    _order = 33  # after CustomEditToolProcessor(30)/OcrQualityAdvisor(32), before CustomSelfVerifyProcessor(90)

    def __init__(self) -> None:
        self._py_script_calls: set[str] = set()
        self._fired: bool = False

    async def on_task_start(self, event):
        self._py_script_calls.clear()
        self._fired = False
        yield event

    async def on_before_tool(self, event):
        if event.tool_name == "Bash":
            command = (event.tool_input or {}).get("command", "") or ""
            if _PY_SCRIPT_RE.search(command):
                self._py_script_calls.add(event.tool_call_id)
        yield event

    async def on_after_tool(self, event):
        if self._fired:
            self._py_script_calls.discard(event.tool_call_id)
            yield event
            return

        was_py_script = event.tool_call_id in self._py_script_calls
        self._py_script_calls.discard(event.tool_call_id)

        result_text = event.result or ""
        if was_py_script and _looks_like_shadow(result_text):
            self._fired = True
            yield dataclasses.replace(event, result=result_text + _ADVISORY)
        else:
            yield event

    async def on_task_end(self, event):
        self._py_script_calls.clear()
        self._fired = False
        yield event
