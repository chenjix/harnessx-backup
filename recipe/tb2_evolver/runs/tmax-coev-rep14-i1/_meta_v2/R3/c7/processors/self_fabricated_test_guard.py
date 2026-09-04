# SPDX-License-Identifier: MIT
"""SelfFabricatedTestGuardProcessor — a TB2 Control processor.

Closes a recurring false-confidence failure shape: the agent builds a
deliverable (a detector / classifier / parser / transformer script), then
"validates" it *only* against test inputs it created itself by echoing a
value it just derived. Because the fabricated input contains the same
derived value the deliverable searches for, the test is a tautology — it
passes even when the derived value is wrong (e.g. a garbled OCR string) and
the ground-truth artifacts already present on disk go unexamined. The
verifier then runs the deliverable against the *real* inputs and it fails.

This is content-agnostic and detects the *shape*, not any task:

  1. Track every filesystem path the agent WRITES to during the run
     (heredocs, redirects, tee, sed -i) — these are "agent-fabricated".
  2. When a later Bash command RUNS the deliverable (invokes an executable
     script / `python3 foo.py` / `bash foo.sh` / `./foo`) and every
     file-path argument it references is an agent-fabricated path — i.e. it
     is being exercised exclusively against inputs the agent manufactured,
     never against a pre-existing task artifact — append a one-shot
     corrective note to the tool result.

The note is append-only on the tool result (mirrors CustomEditToolProcessor),
never terminates, never mutates the system prompt, and fires at most
``max_fires`` times per run so a genuine loop is nudged without spamming.
"""

from __future__ import annotations

import dataclasses
import re
import shlex

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Reuse the same write-detection semantics the edit guard uses.
_REDIRECT_WRITE_RE = re.compile(r"(?<![<>2&\d])>{1,2}\s*([^\s;&|><\n'\"]+)")
_SED_INPLACE_RE = re.compile(r"\bsed\s+(?:-[a-zA-Z]*i[a-zA-Z]*|-i\S*)\s+\S+\s+([^\s;&|><\n'\"]+)")
_TEE_WRITE_RE = re.compile(r"\btee\s+(?:-\S+\s+)*([^\s;&|><\n'\"]+)")
_SKIP_PATH_RE = re.compile(r"^(/dev/|/proc/|/sys/|-|\d+$)")

# Heredoc target: `cat > path << EOF` / `cat >> path <<EOF`
_HEREDOC_RE = re.compile(r">{1,2}\s*([^\s;&|><\n'\"]+)\s*<<-?\s*['\"]?\w")

# A run/execute invocation of a built deliverable.
_INTERP_RE = re.compile(
    r"\b(?:python3?|bash|sh|perl|ruby|node|awk)\b", re.IGNORECASE
)
_EXEC_LOCAL_RE = re.compile(r"(?:^|[\s;&|(])(\./[^\s;&|)]+)")


_NUDGE = (
    "\n\n[SelfFabricatedTestGuard] You just exercised your deliverable using "
    "ONLY input file(s) you created yourself earlier in this run "
    "({paths}). A test built from a value you derived cannot tell you whether "
    "that derived value is correct — it will pass even if the value is wrong "
    "(e.g. a mis-read OCR/scan string, an off-by-one, a wrong delimiter). "
    "Before trusting this result, run the deliverable against the ORIGINAL "
    "input artifacts the task points at — the files that already existed on "
    "disk before you started (evidence images, corpora, sample directories, "
    "databases). If a value came from a lossy source, cross-check it against "
    "those real artifacts (e.g. `strings`/`grep`/`head` the actual target) "
    "rather than against a copy you typed."
)


def _looks_written(cmd: str) -> set[str]:
    """Paths this command writes to (agent-fabricated candidates)."""
    if ">" not in cmd and "sed" not in cmd and "tee" not in cmd:
        return set()
    paths: set[str] = set()
    for pattern in (_HEREDOC_RE, _REDIRECT_WRITE_RE, _SED_INPLACE_RE, _TEE_WRITE_RE):
        for m in pattern.finditer(cmd):
            p = m.group(1).strip("'\"")
            if p and not _SKIP_PATH_RE.match(p):
                paths.add(p)
    return paths


def _basename(p: str) -> str:
    return p.rstrip("/").rsplit("/", 1)[-1]


class SelfFabricatedTestGuardProcessor(MultiHookProcessor):
    """Nudge when a deliverable is validated only against self-created inputs."""

    _singleton_group = "self_fabricated_test_guard"
    _order = 34  # after CustomEditToolProcessor(30), before CustomSelfVerifyProcessor(90)

    def __init__(self, max_fires: int = 2, min_writes: int = 1) -> None:
        self.max_fires = max_fires
        self.min_writes = min_writes
        self._written: set[str] = set()
        self._written_base: set[str] = set()
        self._fires = 0
        self._flag: dict[str, str] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._written.clear()
        self._written_base.clear()
        self._fires = 0
        self._flag.clear()
        yield event

    def _execution_segments(self, cmd: str):
        """Yield (deliverable_token, [arg_tokens]) for each segment that
        actually EXECUTES a program.

        A segment executes a program when, after stripping leading env
        assignments / `nohup` / `time` / `sudo`, its first token is an
        interpreter (python/bash/sh/...) or a path-like executable
        (`./x`, `/path/x`, `x.sh`). Pure display/inspection segments
        (`cat`, `ls`, `echo`, `head`, `grep`, ...) never match, which is
        what keeps this from firing on file dumps.
        """
        _LEADERS = {"nohup", "time", "sudo", "env", "exec", "stdbuf", "setsid"}
        for seg in re.split(r"(?:&&|\|\||[;&|]|\n)", cmd):
            seg = seg.strip()
            if not seg:
                continue
            try:
                parts = shlex.split(seg)
            except ValueError:
                parts = seg.split()
            # strip leading env-assignments and benign leaders
            i = 0
            while i < len(parts) and (parts[i] in _LEADERS or re.match(r"^[A-Za-z_]\w*=", parts[i])):
                i += 1
            if i >= len(parts):
                continue
            head = parts[i]
            rest = parts[i + 1:]
            interp = bool(_INTERP_RE.fullmatch(head)) or bool(
                re.fullmatch(r"python[0-9.]*", head, re.IGNORECASE)
            )
            exec_local = head.startswith("./") or (
                head.startswith("/") and re.search(r"\.(py|sh|pl|rb|js)$", head)
            )
            bare_script = bool(re.search(r"\.(py|sh|pl|rb|js)$", head)) and (
                "/" in head or head in self._written_base or head in self._written
            )
            if not (interp or exec_local or bare_script):
                continue
            # deliverable = the script being run. For an interpreter the
            # deliverable is the first path-like arg; otherwise it's `head`.
            deliverable = None
            arg_tokens: list[str] = []
            if interp:
                for j, tok in enumerate(rest):
                    if tok.startswith("-"):
                        continue
                    if deliverable is None and (
                        "/" in tok or re.search(r"\.(py|sh|pl|rb|js)$", tok)
                    ):
                        deliverable = tok
                    else:
                        if "/" in tok or re.search(r"\.\w{1,5}$", tok):
                            arg_tokens.append(tok)
            else:
                deliverable = head
                for tok in rest:
                    if tok.startswith("-"):
                        continue
                    if "/" in tok or re.search(r"\.\w{1,5}$", tok):
                        arg_tokens.append(tok)
            if deliverable is not None:
                yield deliverable, arg_tokens

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name != "Bash":
            yield event
            return
        cmd = event.tool_input.get("command", "") or ""

        # 1) record fabricated paths from any writes in this command
        for p in _looks_written(cmd):
            self._written.add(p)
            self._written_base.add(_basename(p))

        # 2) detect an execution of an AGENT-WRITTEN deliverable whose input
        #    arguments are ALL agent-fabricated (the tautology shape).
        if self._fires < self.max_fires and len(self._written) >= self.min_writes:
            cur_outputs = _looks_written(cmd)
            cur_output_base = {_basename(o) for o in cur_outputs}
            for deliverable, args in self._execution_segments(cmd):
                dbase = _basename(deliverable)
                deliverable_is_agent = (
                    deliverable in self._written or dbase in self._written_base
                )
                if not deliverable_is_agent:
                    continue  # only guard scripts the agent itself authored
                if not args:
                    continue
                fab_inputs: set[str] = set()
                real_inputs: set[str] = set()
                for tok in args:
                    base = _basename(tok)
                    if tok in cur_outputs or base in cur_output_base:
                        continue  # this run's own output redirect
                    if tok in self._written or base in self._written_base:
                        fab_inputs.add(tok)
                    else:
                        real_inputs.add(tok)
                if fab_inputs and not real_inputs:
                    self._flag[event.tool_call_id] = ", ".join(sorted(fab_inputs))
                    break
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        paths = self._flag.pop(event.tool_call_id, None)
        if paths is not None and self._fires < self.max_fires:
            self._fires += 1
            note = _NUDGE.format(paths=paths)
            yield dataclasses.replace(event, result=(event.result or "") + note)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._written.clear()
        self._written_base.clear()
        self._fires = 0
        self._flag.clear()
        yield event
