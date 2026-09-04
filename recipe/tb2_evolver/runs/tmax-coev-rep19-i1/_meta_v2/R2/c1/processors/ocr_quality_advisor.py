# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""OcrQualityAdvisor — inject a general OCR-quality recipe on first OCR use.

Closes a systemic failure mode observed across TB2 / Tmax tasks that require
reading structured text out of an image with ``tesseract``:

* the agent runs the naive ``tesseract <image> stdout`` (optionally with a
  char whitelist) and gets **garbled** output because the source image has no
  DPI metadata and is small — tesseract falls back to ~70 dpi and mis-reads
  dense/small glyphs;
* the agent then either fabricates the schema from a handful of visible sample
  values or trusts the garbled string verbatim, and the downstream artifact
  (a parser, a detector, an extracted key) is subtly wrong. The task exits
  ``done`` but fails the verifier on a *correctness* metric (accuracy below
  threshold, key never matches), not on a loop or budget signal.

The two shapes seen: (a) an image whose full structured content is needed —
the garbled OCR loses field mappings and the parser scores ~1/3 accuracy;
(b) an image holding a single long token (an SSH key) where a handful of
mis-read base64 characters makes the extracted value never match.

This is a **capability-adjacent** gap: the OCR tool is present and the agent
reaches for it, but it does not apply the standard image-OCR quality recipe
(upscale the image several-fold, force a sane ``--dpi``, try alternate page
segmentation modes, and *verify the output is legible* before trusting it).
The existing ``RepeatedCommandBreaker`` only fires when the agent re-issues a
*byte-identical* command; here the agent typically varies the command slightly
(contrast tweaks, whitelist changes) so the loop breaker never trips, yet none
of the variations fix the underlying resolution problem.

Mechanism: an ``on_after_tool`` hook watches Bash commands for an OCR
invocation (``tesseract`` on the command line, or a ``pytesseract`` /
``image_to_string`` call in an inline Python snippet). On the **first** such
invocation in a task it appends a one-time advisory to that tool's result
describing the general OCR-quality recipe. It fires once per task (so it never
becomes noise) and only augments ``event.result`` — a contract-safe mutation
that never inserts or edits messages.

Generalisation: content-agnostic. It keys purely on the presence of an OCR
command, names no task, no image path, no schema field, no constant. The
advisory is a *strategy* ("upscale, set dpi, try psm modes, verify
legibility"), not a solution — it would help any unseen task that must OCR an
image, and is inert on every task that never invokes OCR.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Detect an OCR invocation inside a Bash command string. Matches the tesseract
# CLI as a token, or a pytesseract / image_to_string call in an inline snippet.
_OCR_RE = re.compile(
    r"(?:\btesseract\b|\bpytesseract\b|\bimage_to_string\b)",
    re.IGNORECASE,
)

_ADVISORY = (
    "\n\n[OcrQualityAdvisor] You just ran OCR on an image. Naive OCR on a "
    "small or metadata-less image is frequently GARBLED (tesseract commonly "
    "warns 'Invalid resolution 0 dpi. Using 70 instead' and then mis-reads "
    "dense or small text). Before you trust this output or build any parsing "
    "logic on top of it, treat the extracted text as UNVERIFIED and improve "
    "OCR quality:\n"
    "  1. UPSCALE the image several-fold (e.g. 3x-4x with a good resampling "
    "filter) and re-run OCR on the enlarged copy — small glyphs read far "
    "better at higher pixel density.\n"
    "  2. Force a sane resolution with '--dpi 300' and try binarising / "
    "increasing contrast on a grayscale copy.\n"
    "  3. Try several page-segmentation modes ('--psm 6' for a uniform block, "
    "'--psm 4', '--psm 11') and compare — the default mode is often wrong for "
    "tables or multi-line layouts.\n"
    "  4. VERIFY legibility: read the output back and check it is coherent "
    "(valid words, consistent field names, plausible characters). If it is "
    "still garbled, keep improving the image — do NOT guess the content from "
    "a few visible sample values and do NOT trust a mis-read string; a "
    "fabricated or mis-transcribed value will fail exact-match / accuracy "
    "verification on the hidden dataset.\n"
    "  5. Only once the OCR output is clearly legible should you encode it "
    "into your parser / extractor."
)


class OcrQualityAdvisor(MultiHookProcessor):
    """Append a one-time general OCR-quality recipe on the first OCR command.

    Parameters
    ----------
    tool_name:
        Which tool to watch (TB2/Tmax only expose ``Bash``).
    """

    _singleton_group = "ocr_quality_advisor"
    _order = 32  # after RepeatedCommandBreaker (31); appends to tool result

    def __init__(self, tool_name: str = "Bash") -> None:
        self.tool_name = tool_name
        self._fired = False
        self._pending: set[str] = set()

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._pending.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == self.tool_name and not self._fired:
            command = (
                event.tool_input.get("command", "") if event.tool_input else ""
            )
            if command and _OCR_RE.search(command):
                self._pending.add(event.tool_call_id)
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if event.tool_call_id in self._pending:
            self._pending.discard(event.tool_call_id)
            if not self._fired:
                self._fired = True
                yield dataclasses.replace(
                    event, result=(event.result or "") + _ADVISORY
                )
                return
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._pending.clear()
        yield event
