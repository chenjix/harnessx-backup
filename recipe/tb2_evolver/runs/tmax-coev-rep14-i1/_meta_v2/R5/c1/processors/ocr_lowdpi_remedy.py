# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""OcrLowDpiRemedyProcessor for Tmax (TB2-style) agents.

Closes a concrete, mechanical harness deficiency in OCR tasks.

Failure shape (observed):
    `tesseract <image> stdout` on a PNG that carries no DPI metadata emits
    ``Warning: Invalid resolution 0 dpi. Using 70 instead.`` and then estimates
    a low effective resolution. On a *dense* image (a multi-row table / small
    font), 70 dpi is far below tesseract's optimal ~300 dpi, so the text OCRs
    to garbage. The agent then cycles through grayscale / contrast / binarize /
    sharpen / multiple ``--psm`` modes — all of which are *contrast* remedies —
    but never applies the single most effective *resolution* remedy: upscaling
    the image so its effective DPI reaches ~300 (and/or passing ``--dpi 300``).
    Unable to read the schema, the agent guesses it from a few sample inputs and
    ships a script that fails the hidden-set accuracy threshold.

Why a processor, not the system prompt:
    A prior system-prompt append that told the agent to "exhaust OCR remedies"
    was reverted for global regression — it inflated the prompt on every task,
    OCR or not. This processor is inert on every non-OCR task and on OCR runs
    that don't trip the low-DPI warning: it fires ONLY when the tesseract
    low-DPI warning is actually present in a tool result AND the command that
    produced it did not already upscale/`--dpi` the image. It appends one
    content-agnostic remedy note to that tool result. No task literals, no
    schema content, no route names — just a documented tesseract operational
    fact that applies to any low-DPI OCR task.

Contract: append-only on ``on_after_tool`` (mirrors CustomEditToolProcessor),
never removes messages, never mutates the system prompt, never terminates.
Fires at most ``max_fires`` times per run so a stubborn image is nudged without
spamming.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    TaskStartEvent,
    TaskEndEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# The distinctive tesseract signal that its input resolution is too low to OCR
# reliably. Matches "Invalid resolution 0 dpi", "Using 70 instead",
# "Estimating resolution as NNN" — any of these indicates a missing/low DPI.
_LOWDPI_SIGNAL = re.compile(
    r"Invalid resolution\s+\d+\s*dpi"
    r"|Using\s+\d+\s+instead"
    r"|Estimating resolution as\s+\d+",
    re.IGNORECASE,
)

# Signs the command already applied a resolution remedy — if so, don't re-nudge.
_ALREADY_UPSCALED = re.compile(
    r"\.resize\("
    r"|--dpi\b"
    r"|-density\b"          # imagemagick convert -density
    r"|LANCZOS|BICUBIC|resample"
    r"|\bscale\s*=|\bupscal",
    re.IGNORECASE,
)

# Only nudge when the offending command actually invoked tesseract, so an
# unrelated command whose output happens to echo the phrase never fires.
_TESSERACT_CMD = re.compile(r"\btesseract\b|pytesseract|image_to_string", re.IGNORECASE)

_REMEDY_NOTE = (
    "\n\n[harness OCR hint] The tesseract output above reports a very low input "
    "resolution (no/low DPI). At ~70 effective DPI, dense or small text OCRs to "
    "garbage no matter how you tweak contrast, binarization, or --psm. The single "
    "most effective fix is to RAISE THE RESOLUTION before OCR: load the image "
    "(PIL: Image.open(path)), upscale it ~3-4x with a high-quality resampler "
    "(img.resize((w*4, h*4), Image.LANCZOS)), save it, then run tesseract on the "
    "upscaled file (optionally add `--dpi 300`). Try this before concluding the "
    "text is unreadable, and do NOT infer the required mapping/schema from a few "
    "sample inputs while the image is still garbled — the hidden evaluation set "
    "contains cases the samples do not reveal, so a guessed schema will score low. "
    "Only fall back to inference after the upscale+re-OCR attempt genuinely fails."
)


class OcrLowDpiRemedyProcessor(MultiHookProcessor):
    """Inject a concrete upscale-before-OCR remedy when tesseract reports low DPI."""

    _singleton_group = "ocr_lowdpi_remedy"
    _order = 34  # after CustomEditToolProcessor (30), before CustomSelfVerifyProcessor (90)

    def __init__(self, max_fires: int = 2) -> None:
        self.max_fires = max(1, int(max_fires))
        self._fires: int = 0
        self._cmd_by_id: dict[str, str] = {}

    async def on_task_start(self, event: TaskStartEvent):
        self._fires = 0
        self._cmd_by_id.clear()
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") or ""
            self._cmd_by_id[event.tool_call_id] = cmd
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        cmd = self._cmd_by_id.pop(event.tool_call_id, "")
        if self._fires >= self.max_fires:
            yield event
            return

        result = event.result or ""
        combined = cmd + "\n" + result

        # Fire only when: (a) a tesseract-family OCR call was made, (b) the
        # low-DPI signal is present, (c) the command didn't already upscale.
        if (
            _TESSERACT_CMD.search(combined)
            and _LOWDPI_SIGNAL.search(result)
            and not _ALREADY_UPSCALED.search(cmd)
        ):
            self._fires += 1
            yield dataclasses.replace(event, result=result + _REMEDY_NOTE)
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fires = 0
        self._cmd_by_id.clear()
        yield event
