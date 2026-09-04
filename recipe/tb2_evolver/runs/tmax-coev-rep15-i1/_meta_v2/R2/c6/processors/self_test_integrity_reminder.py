# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""SelfTestIntegrityReminderProcessor.

Closes a *verification-integrity* failure mode observed on the tmax /
terminal-bench-2 evaluation: a task requires the agent to extract a value from
a lossy or authoritative source (an SSH key read out of a PNG via OCR, a schema
mapping OCR'd from an image, a token decoded from a blob) and to build a
deliverable that depends on that extracted value being exactly right. The agent
then "verifies" the deliverable — but only against a *fixture it fabricated in
the same session by echoing the very value it just extracted*. That self-test
is circular: the fixture contains, by construction, the exact string the
deliverable searches for, so the test passes whether or not the extraction was
correct. The external grader instead tests the deliverable against the *real*
provided inputs (a trojaned-binary corpus, a golden dataset), where a
mis-extracted value never matches — and the task scores 0.

Observed instances (same mechanism, different inputs):

* task_000505_50b5162d — an SSH public key is OCR'd from ``/app/evidence.png``
  with visible garble; the agent hardcodes the garbled key into
  ``detect_trojan.sh`` and tests it by ``echo``-ing the same garbled key into
  ``/tmp/malicious_test.txt`` (detected, "correct"). The real evil corpus
  (``ls_evil``, ``cat_evil``) carries the *correct* key, so 2/2 evil bypass.
* task_000015_89886d8d — a routing schema is OCR'd from a PNG; the agent tests
  its migrate script only on the 3-line provided ``sample_urls.txt`` (a
  self-selected trivial fixture), never on the golden corpus the grader uses.
  Accuracy 0.0000 on the real corpus.

The agent has one tool (``Bash``) and cannot see the grader's test files (they
are injected after it exits), so it has no structural way to know its self-test
is circular. This processor supplies that missing context as a *mechanical,
one-shot* exit-intent nudge: when the agent has demonstrably (a) run an
extraction tool AND (b) built its verification fixture by writing extracted text
into a file it then tests against, and then tries to finish, it is reminded to
re-verify the extracted value against the authoritative source and to exercise
the deliverable against any *real provided* inputs rather than only
self-fabricated fixtures.

This is a *class* fix, not a task fix: it fires on any task exhibiting the
extraction + circular-self-test shape, keyed off the agent's own Bash activity,
never off task identifiers.
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
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor


# Signal A — the agent ran a tool that extracts a value from a lossy /
# authoritative source. Intentionally broad; a false positive only costs one
# cheap advisory, a false negative re-introduces the silent circular-test win.
_EXTRACTION_RE = re.compile(
    r"""
    (?:\btesseract\b)                  # OCR CLI
    | (?:pytesseract)                  # OCR python binding
    | (?:image_to_string)              # OCR python api
    | (?:\bocr\b)                      # generic OCR invocation
    | (?:gocr|ocrad|easyocr|paddleocr) # other OCR engines
    | (?:\bstrings\b.*\|.*grep)        # strings-then-grep extraction from a binary
    | (?:\bxxd\b|\bhexdump\b)          # decode a value out of a blob
    | (?:base64\s+(?:-d|--decode))     # decode an encoded value
    | (?:zbarimg|qrdecode)             # decode a code out of an image
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Signal B — the agent fabricated a test fixture by writing text into a file it
# then feeds to its own deliverable. Matches the common shapes:
#   echo "..." > /tmp/x        printf "..." > x        cat > x <<'EOF' ...
_FIXTURE_WRITE_RE = re.compile(
    r"""
    (?:\becho\b[^\n|]*>\s*\S)          # echo "..." > file  (redirect to a path)
    | (?:\bprintf\b[^\n|]*>\s*\S)      # printf "..." > file
    | (?:\bcat\b\s*>\s*\S+.*<<)        # cat > file <<EOF   (heredoc fixture)
    | (?:\btee\b\s+\S)                 # ... | tee file
    """,
    re.IGNORECASE | re.VERBOSE,
)


_INTEGRITY_TOOL = "_self_test_integrity_reminder"
_INTEGRITY_ACK = "Verification-integrity check acknowledged. See the message above."

_INTEGRITY_MSG = """\
[VerificationIntegrityCheck] Your solution depends on a value you EXTRACTED from a \
source (e.g. OCR of an image, `strings`/decode of a binary), and the tests you ran \
appear to use fixtures you CREATED in this session by writing that same extracted \
value into a file. That kind of self-test is circular: the fixture contains, by \
construction, the exact value your deliverable looks for, so it passes whether or \
not your extraction was correct. An automated grader will instead run your \
deliverable against the REAL inputs provided by the task — where a mis-extracted \
value (a single wrong character, a dropped/added space, a truncated tail, an OCR \
look-alike like l/1, O/0, I/1) will silently fail to match and score 0.

Before finishing, do BOTH of these:

1. Re-verify the extracted value against its authoritative source, not against your \
   own transcription. If it came from OCR, re-run the extraction with different \
   settings (e.g. upscale the image, `--dpi 300`, sweep `--psm`, binarize) and \
   compare character-by-character; reconcile any look-alike characters and stray \
   whitespace. If it came from a binary/blob, dump the exact bytes and compare.

2. Exercise your deliverable against the REAL inputs the task refers to, not only \
   fixtures you built from the extracted value. Enumerate what the task actually \
   provides (list the relevant directories/files, look for a real corpus / sample \
   set / target artifacts) and run your deliverable against those. A clean pass on a \
   fixture you fabricated proves nothing if the real inputs differ.

If, after this, you are confident the extracted value and the deliverable are \
correct on the real inputs, then finish."""


class SelfTestIntegrityReminderProcessor(MultiHookProcessor):
    """One-shot exit-intent reminder against circular self-tests.

    Fires at most once per task, and only when the agent's Bash activity has
    matched BOTH an extraction signal (Signal A) and a self-fabricated-fixture
    signal (Signal B). Coordinates with any other exit-intent processor by only
    acting on a turn that still has no tool calls: if another processor has
    already converted the current exit attempt into a keepalive tool call, this
    processor stays silent and fires on the next genuine exit attempt instead.
    """

    _singleton_group = "self_test_integrity_reminder"
    # Run after CustomSelfVerifyProcessor (_order=90) and ServiceDepsReminder
    # (_order=91) so the exit-intent hooks serialize rather than all rewriting
    # tool_calls on the same turn.
    _order = 93

    def __init__(self) -> None:
        self._extraction_seen = False
        self._fixture_seen = False
        self._reminded = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._extraction_seen = False
        self._fixture_seen = False
        self._reminded = False
        self._pending_message = ""
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        # Absorb our own keepalive tool call so it never reaches the sandbox.
        if event.tool_name == _INTEGRITY_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_INTEGRITY_ACK
            )
            return
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") or ""
            if _EXTRACTION_RE.search(cmd):
                self._extraction_seen = True
            if _FIXTURE_WRITE_RE.search(cmd):
                self._fixture_seen = True
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
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        armed = self._extraction_seen and self._fixture_seen
        if exit_intent and armed and not self._reminded:
            self._reminded = True
            self._pending_message = _INTEGRITY_MSG
            keepalive = ToolCall(
                id=f"sti-{uuid.uuid4().hex[:8]}",
                name=_INTEGRITY_TOOL,
                input={},
            )
            yield dataclasses.replace(event, tool_calls=(keepalive,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._extraction_seen = False
        self._fixture_seen = False
        self._reminded = False
        self._pending_message = ""
        yield event
