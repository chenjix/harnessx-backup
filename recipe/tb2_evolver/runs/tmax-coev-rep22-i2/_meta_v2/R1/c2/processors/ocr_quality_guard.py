# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""OcrQualityGuardProcessor for Tmax (and TB2-style) agents.

Closes a systemic failure mode observed across multiple image-OCR tasks:
the agent runs ``tesseract`` on a PNG whose file carries **no DPI metadata**.
Tesseract emits ``Warning: Invalid resolution 0 dpi. Using 70 instead.`` and
then OCRs the image at a degraded ~70 dpi, producing garbled text
(``0``/``O`` confusion, ``1``/``l``/``I`` confusion, dropped characters,
mangled symbols). The agent, unaware of the standard remediation, thrashes
with contrast/threshold/PSM tweaks — which do not touch the resolution
problem — then gives up and *guesses* the content from context. The guess is
close but wrong on the exact tokens that matter (a schema mapping, an SSH
key, a CRC polynomial constant), so the verifier fails on an exact-match /
accuracy check.

The fix is a general, benchmark-agnostic mechanism, not task knowledge:
when a tool result shows the low-resolution warning from an OCR engine, the
processor appends a **one-time** hint describing the standard OCR-quality
remediation (rescale the image to a higher DPI before OCR, or pass an
explicit ``--dpi``). The hint contains no task-specific constants, paths, or
answers — it only names a technique that applies to *any* low-DPI OCR input.

Trigger is purely observable in tool output (the engine's own warning), so
the processor is silent on every task that does not hit this failure shape.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    TaskStartEvent,
    TaskEndEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Tesseract (and some other engines) print this when the image file has no
# embedded resolution metadata. It is the canonical signal that OCR quality
# is being silently capped far below what the source pixels support.
_LOW_DPI_RE = re.compile(
    r"invalid\s+resolution\s+\d+\s*dpi|estimating\s+resolution\s+as\s+\d+",
    re.IGNORECASE,
)

_OCR_QUALITY_HINT = (
    "\n\n[OcrQualityGuard] The OCR engine reported that the source image has "
    "no embedded resolution, so it fell back to a very low DPI. At that DPI "
    "the recognised text is usually garbled on exactly the characters that "
    "matter (0/O, 1/l/I, S/5, punctuation, sub/superscripts), and no amount "
    "of contrast/threshold/PSM tweaking recovers it — the pixels are simply "
    "being read too small. The standard remediation is to RESCALE the image "
    "to a higher effective DPI before OCR (e.g. upsample it ~3-4x with a "
    "high-quality resample filter, or re-save it with an explicit high DPI, "
    "then re-run OCR at ~300 dpi). Do this and re-run OCR before you rely on "
    "the extracted text; if a value looks ambiguous, cross-check it against "
    "any sample/expected data in the task. Do not guess the exact tokens from "
    "garbled output — an exact-match check will fail on a near-miss."
)


class OcrQualityGuardProcessor(MultiHookProcessor):
    """Detect low-DPI OCR fallback in tool output and nudge the standard fix.

    Fires at most ``max_hints`` times per task (default once): the goal is to
    surface the remediation technique the first time the failure shape
    appears, not to spam it on every subsequent OCR call.
    """

    _singleton_group = "ocr_quality_guard"
    _order = 31  # near CustomEditToolProcessor (30); both append to tool result

    def __init__(self, max_hints: int = 1) -> None:
        self.max_hints = max(1, int(max_hints))
        self._hints_emitted: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._hints_emitted = 0
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if self._hints_emitted >= self.max_hints:
            yield event
            return
        text = (event.result or "")
        if event.error:
            text = text + "\n" + str(event.error)
        if text and _LOW_DPI_RE.search(text):
            self._hints_emitted += 1
            yield dataclasses.replace(
                event, result=(event.result or "") + _OCR_QUALITY_HINT
            )
            return
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._hints_emitted = 0
        yield event
