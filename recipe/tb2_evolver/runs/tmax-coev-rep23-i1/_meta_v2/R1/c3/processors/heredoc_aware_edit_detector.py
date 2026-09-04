# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""Heredoc-aware Bash edit detector for TB2.

Motivation
----------
The stock ``benchmarks.terminal_bench_2.harness.CustomEditToolProcessor``
counts file "edits" by scanning a Bash command for write redirects
(``>``, ``>>``, ``sed -i``, ``tee``).  On TB2 the only tool is ``Bash``,
so the dominant file-authoring idiom is a single heredoc write:

    cat > /home/user/foo.py << 'EOF'
    ... python source ...
    EOF

The stock ``_extract_written_files`` scans the *entire* command string,
heredoc body included.  Any ``>`` inside the body — e.g. a Python
comparison ``if size > max_size:`` or an awk clause ``> $ERROR_THRESHOLD``
— is misread as a write redirect, producing bogus "written files" such as
``max_size:``, ``SIZE_THRESHOLD:``, ``=`` or ``0.02,``.  These phantom
paths inflate the per-file edit counter and, worse, fire spurious
``[EditDetection] File \`SIZE_THRESHOLD:\` has been modified more than N
times`` warnings.  Observed across multiple TB2 trajectories; on
task_000118 three phantom warnings fired at once and drove the model into
an unproductive delete-and-recreate loop that never reached the real bug.

Fix
---
Strip heredoc bodies before extracting write targets, so a single
``cat > file << EOF ... EOF`` counts as exactly one edit to exactly one
file (the redirect target on the opening line) and the body's ``>``
characters are treated as data, not commands.  Legitimate over-edit
detection on genuinely repeated writes is preserved unchanged.

This class is otherwise a drop-in replacement for the stock
``CustomEditToolProcessor`` — same hook wiring, same singleton group,
same ``threshold`` knob, same warning text.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
    TaskEndEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Mirror the stock patterns so behaviour on non-heredoc commands is identical.
_REDIRECT_WRITE_RE = re.compile(r"(?<![<>2&\d])>{1,2}\s*([^\s;&|><\n'\"]+)")
_SED_INPLACE_RE = re.compile(
    r"\bsed\s+(?:-[a-zA-Z]*i[a-zA-Z]*|-i\S*)\s+\S+\s+([^\s;&|><\n'\"]+)"
)
_TEE_WRITE_RE = re.compile(r"\btee\s+(?:-\S+\s+)*([^\s;&|><\n'\"]+)")
_SKIP_PATH_RE = re.compile(r"^(/dev/|/proc/|/sys/|-|\d+$)")

# Matches a heredoc opener and captures the delimiter word.  Handles the
# common forms:  << EOF, <<-EOF, << 'EOF', << "EOF", <<EOF
_HEREDOC_OPEN_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

_EDIT_LIMIT_WARN = (
    "\n\n[EditDetection] File `{path}` has been modified more than {threshold} times. "
    "You are over-editing this file — step back and try a fundamentally different approach."
)


def _strip_heredoc_bodies(cmd: str) -> str:
    """Remove the body of every heredoc in ``cmd``, keeping the opener line.

    The opener line is retained so the ``cat > file << EOF`` redirect target
    is still counted; only the interior lines (up to and including the
    closing delimiter) are dropped.  If a heredoc is unterminated (no closing
    delimiter found) the remainder of the command is dropped, which is the
    safe choice — an unterminated body cannot contain real write commands
    for our purposes.
    """
    lines = cmd.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        out.append(line)
        # A single physical line can open multiple heredocs; the delimiters
        # close in the order they were opened.
        openers = _HEREDOC_OPEN_RE.findall(line)
        if openers:
            delimiters = [d for _, d in openers]
            i += 1
            # Consume body lines until every delimiter has been closed.
            while i < n and delimiters:
                body_line = lines[i]
                if body_line.strip() == delimiters[0]:
                    delimiters.pop(0)
                i += 1
            continue
        i += 1
    return "\n".join(out)


def _extract_written_files(cmd: str) -> list[str]:
    """Return deduplicated file paths that a bash command writes to.

    Heredoc bodies are stripped first so ``>`` characters inside written
    file content are not misread as redirects.
    """
    scan = _strip_heredoc_bodies(cmd)
    if ">" not in scan and "sed" not in scan and "tee" not in scan:
        return []
    paths: set[str] = set()
    for pattern in (_REDIRECT_WRITE_RE, _SED_INPLACE_RE, _TEE_WRITE_RE):
        for m in pattern.finditer(scan):
            p = m.group(1).strip("'\"")
            if p and not _SKIP_PATH_RE.match(p):
                paths.add(p)
    return list(paths)


class HeredocAwareEditToolProcessor(MultiHookProcessor):
    """Detect repeated Bash-based file edits, ignoring heredoc body content.

    Drop-in replacement for the stock ``CustomEditToolProcessor``: same hook
    surface, same ``threshold`` semantics, same warning text.  The only
    behavioural difference is that ``cat > file << EOF ... EOF`` counts as a
    single edit of ``file`` and the heredoc body's ``>`` characters no longer
    manufacture phantom file paths.
    """

    _singleton_group = "bash_edit_detector"
    _order = 30

    def __init__(self, threshold: int = 7) -> None:
        self.threshold = threshold
        self._edit_counts: dict[str, int] = {}
        self._pending: dict[str, list[str]] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._edit_counts.clear()
        self._pending.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            paths = _extract_written_files(event.tool_input.get("command", ""))
            if paths:
                self._pending[event.tool_call_id] = paths
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        paths = self._pending.pop(event.tool_call_id, [])
        injections: list[str] = []
        for path in paths:
            count = self._edit_counts.get(path, 0) + 1
            self._edit_counts[path] = count
            if count > self.threshold:
                injections.append(
                    _EDIT_LIMIT_WARN.format(path=path, threshold=self.threshold)
                )
                self._edit_counts[path] = 0
        if injections:
            yield dataclasses.replace(
                event, result=(event.result or "") + "".join(injections)
            )
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._edit_counts.clear()
        self._pending.clear()
        yield event
