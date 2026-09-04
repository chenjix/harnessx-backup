# SPDX-License-Identifier: MIT
"""RealArtifactVerifyProcessor.

A drop-in replacement for the TB2 ``CustomSelfVerifyProcessor`` that keeps the
exact same firing mechanism (one-shot keepalive tool-call injected when the
model tries to exit with no tool calls, followed by a single appended user
message) but conditionally strengthens the checklist to close a specific,
recurring *self-referential validation* failure shape.

Motivation
----------
On several tasks the deliverable is a program that derives a value from a lossy
or secondary source (OCR of an image, a sampled schema, an extracted id) and
then classifies / transforms / audits a *real* input using that derived value.
The agent finishes ``done`` / ``no_tool_calls`` fully confident, yet fails,
because it "validated" its deliverable against **inputs it fabricated itself** —
e.g. it writes a temp file by ``echo``-ing the very string it derived, then runs
its detector on that file and sees the expected result. That test is guaranteed
to pass *even when the derived value is wrong*: it never exercises the real
artifacts the grader uses.

Observed instances of this exact shape (voluntary exit, wrong output on the real
input, self-fabricated test used as "proof"):

  - a trojan-detector script tested only against a file the agent echoed its own
    (garbled) key into — the real trojaned binaries on disk were never grepped;
  - a parser/transformer "verified" only against the handful of sample rows the
    agent inferred its rules from, so hidden real inputs were never exercised;
  - an audit script whose output was eyeballed rather than diffed against the
    real data source already present in the environment.

The root cause is a *verification-discipline* gap, not a missing capability or a
model reasoning error the harness could fix directly: the agent has every tool
it needs (Bash, the real files are already on disk during the agent phase), it
simply trusts a circular test. The correct general discipline is: exercise the
deliverable against the REAL artifacts named in the task / already present in the
environment, never against inputs you constructed from a value you derived.

Design
------
This processor detects the antipattern *mechanically* at runtime so the extra
guidance is delivered only when the shape is present:

  * ``on_before_tool`` scans each Bash command for the "write-a-literal-into-a
    -file then run something against it" fingerprint — a here-doc or ``echo``/
    ``printf`` redirected into a file (a self-authored fixture), combined with a
    later invocation of an executable/script against a file path.

  * On voluntary exit, if that fingerprint fired during the run, the injected
    checklist adds ONE extra, task-agnostic item telling the agent to re-run its
    deliverable against the REAL artifacts the task points at (survey the
    environment with ``find`` / ``ls`` first) rather than a self-constructed
    input, because a fixture built from a value it derived cannot catch a
    derivation/extraction error.

  * When the fingerprint never fired, the injected checklist is **byte-for-byte
    the stock text** — so tasks that do not exhibit the antipattern behave
    exactly as under the stock ``CustomSelfVerifyProcessor`` (zero regression
    surface).

No task-specific constants, paths, identifiers, or copy-paste code — only the
content-agnostic structural fingerprint of a self-referential test.

Contract
--------
Identical to the class it replaces: at most one appended user message
(role="user") per task run, injected on the turn following the exit attempt; a
synthetic keepalive tool call keeps the loop alive so the message can be
delivered. No system-prompt mutation, no message removal, +1 message max.
"""

from __future__ import annotations

import dataclasses
import re
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

_VERIFY_TOOL = "_tb2_self_verify"
_VERIFY_ACK = "Verification check initiated. See the message above for instructions."

# A here-doc or echo/printf that redirects a literal into a file: the shape of a
# self-authored test fixture. Deliberately loose and content-agnostic.
_FIXTURE_WRITE_RE = re.compile(
    r"""(?:
            <<[-']?\s*['"]?\w+                       # here-doc:  << 'EOF'
          | \b(?:echo|printf)\b[^\n|;&]*?>{1,2}\s*\S # echo ... > file
        )""",
    re.VERBOSE,
)

# Invocation of a script/executable against a file argument, e.g.
# `./detect.sh /tmp/x`, `bash foo.sh <path>`, `python3 mig.py sample.csv`,
# `/home/user/script.sh $F`. Content-agnostic.
_RUN_AGAINST_FILE_RE = re.compile(
    r"""(?:
            \./\S+                                   # ./something
          | \b(?:bash|sh|python3?|perl|ruby)\s+\S+\.\w+  # interpreter script.ext
          | /\S+/\S+\.(?:sh|py|pl|rb)                # /abs/path/script.sh
        )\s+\S""",
    re.VERBOSE,
)

_STOCK_MSG = """\
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

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""

# Same checklist, with one extra item (4b) inserted before the generic
# "validate your verification method" step. Task-agnostic.
_REAL_ARTIFACT_MSG = """\
Before finishing, run through this checklist — do not skip any step even if you already verified:

1. **Re-read the task description now.** Does your solution address every requirement, including edge cases, accuracy thresholds, and exact output format?

2. **Check every required output file exists at its exact path:**
```bash
ls -lh /path/to/each/required/output/file
```
A script that ran without errors does NOT guarantee the file was written. Run `ls` explicitly for each required file.

3. **Inspect the actual file contents** — `cat` or `head` each output file and confirm the values are semantically correct, not just that the file exists or is non-empty.

4. **Exercise your deliverable against the REAL inputs, not a fixture you built.** It looks like you tested your program on a file you created yourself (an `echo`/here-doc fixture). A test whose input you constructed from a value you derived (from OCR, a scan, a sample, or any extraction step) will pass even when that derived value is WRONG — it only proves the program is self-consistent, not correct. Before trusting it:
   - Survey the environment for the real artifacts the task refers to (`find / -name '<pattern>' 2>/dev/null`, `ls -R` the relevant directories); the real inputs the grader uses are already present now.
   - Run your program end-to-end on those real files and confirm the result is what the task requires.
   - If a value came from a lossy source, cross-check it against an authoritative on-disk source (e.g. the real target files) before trusting it.

5. **Validate your verification method.** Did your test actually exercise the real behavior? Tests that only check syntax, importability, or exit code 0 on a trivial case are NOT valid — they can pass even on a broken implementation.

6. **For running services:** confirm they are still alive and reachable right now, not just that they started earlier.

Fix anything that looks wrong before exiting. When all checks pass, end your final message with:
**SUCCESS: task complete. Output files confirmed at [list each required path].**\
"""


class RealArtifactVerifyProcessor(MultiHookProcessor):
    """One-shot self-verify that upgrades the checklist when a self-referential
    test fixture was detected during the run.

    Fires at most once per task run. Mirrors the TB2 self-verify mechanism
    (keepalive synthetic tool call + single appended user message). When no
    self-fabricated-fixture signal was seen, the injected message is byte-for-byte
    the stock checklist.
    """

    _singleton_group = "tb2_self_verify"
    _order = 90

    def __init__(self) -> None:
        self._verified = False
        self._pending_message: str = ""
        self._saw_fixture_write = False
        self._saw_run_against_file = False

    async def on_task_start(self, event: TaskStartEvent):
        self._verified = False
        self._pending_message = ""
        self._saw_fixture_write = False
        self._saw_run_against_file = False
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _VERIFY_TOOL:
            yield dataclasses.replace(event, approved=False, synthetic_result=_VERIFY_ACK)
            return
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") or ""
            if _FIXTURE_WRITE_RE.search(cmd):
                self._saw_fixture_write = True
            if _RUN_AGAINST_FILE_RE.search(cmd):
                self._saw_run_against_file = True
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
            # Antipattern present only when the agent both authored a fixture
            # AND ran an executable against a file during the run.
            if self._saw_fixture_write and self._saw_run_against_file:
                self._pending_message = _REAL_ARTIFACT_MSG
            else:
                self._pending_message = _STOCK_MSG
            keepalive = ToolCall(
                id=f"sv-{uuid.uuid4().hex[:8]}",
                name=_VERIFY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._verified = False
        self._pending_message = ""
        self._saw_fixture_write = False
        self._saw_run_against_file = False
        yield event
