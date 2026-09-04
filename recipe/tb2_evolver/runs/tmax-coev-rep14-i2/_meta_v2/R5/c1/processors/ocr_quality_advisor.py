# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""OcrQualityAdvisor — a TB2 control-lever MultiHookProcessor.

Problem class this closes
-------------------------
Some TB2 tasks require reading text out of an image with ``tesseract`` (an
image-embedded schema, a hidden key, a config sketch, …).  The images shipped
with these tasks frequently carry **no DPI metadata and are physically small**,
so tesseract falls back to a low default resolution and emits garbled output.
Its own stderr flags this exactly:

    Warning: Invalid resolution 0 dpi. Using 70 instead.
    Estimating resolution as 111

An agent that trusts that first garbled read then builds a downstream artifact
(a parser, a detector, a mapping) on top of misread characters — the artifact
looks plausible but is wrong, and the verifier's exact-match / accuracy test
fails.  Observed on two distinct-domain tasks in this trajectory set: an OCR'd
routing schema that produced a wrong URL mapper (task_000015; verifier accuracy
threshold 0.98), and an OCR'd SSH key whose character confusions (``I``/``l``/
``1``, ``0``/``O``, ``5``/``S``, spurious spaces) made an exact-string detector
miss every adversarial sample (task_000505; "2 of 2 evil bypassed").

The single most effective, entirely generic tesseract remedy for this input
shape — **upscale the image ~3-4x and pass an explicit ``--dpi 300``, plus
grayscale + binarize** — was never tried in either trajectory; the agent kept
re-running near-identical contrast/PSM tweaks on the original resolution.

What this processor does
------------------------
Purely mechanical, contract-safe, and general (no task-specific literals):

* ``on_before_tool``  — remember which Bash calls actually invoked tesseract /
  pytesseract, so the advisory only ever attaches to a real OCR call.
* ``on_after_tool``   — if that call's result carries tesseract's own
  low-resolution signal (``Invalid resolution`` / ``Estimating resolution`` /
  ``Using N instead``), append a **one-time** advisory describing the generic
  high-yield preprocessing recipe and reminding the agent to cross-check
  ambiguous glyphs before building anything on the extracted text.

Fires **at most once per task** and **only** when a tesseract call emitted the
low-resolution signal, so it is invisible to the (large) majority of non-OCR
tasks — zero added cost or regression surface there.  It mutates only the
tool-result string (the same shape as ``CustomEditToolProcessor``); it never
inserts, drops, or reorders messages.
"""
from __future__ import annotations

import dataclasses
import re

from harnessx.core.processor import MultiHookProcessor

# --- detection --------------------------------------------------------------

# The agent asked tesseract to read an image (CLI or the pytesseract binding).
_OCR_INVOKE_RE = re.compile(r"\btesseract\b|pytesseract|image_to_string", re.IGNORECASE)

# Tesseract's own signal that the image lacks DPI metadata / is being read at a
# guessed (usually too-low) resolution — the exact case where upscale + --dpi
# pays off most.
_LOW_RES_SIGNAL_RE = re.compile(
    r"Invalid resolution|Estimating resolution|Using \d+ instead", re.IGNORECASE
)

_ADVISORY = (
    "\n\n[OcrQualityAdvisor] This tesseract call reported a missing / guessed "
    "image resolution, which is the most common cause of garbled OCR on small "
    "images. Before trusting this text (or building any parser/detector/mapping "
    "on top of it), re-run OCR with the standard high-accuracy preprocessing you "
    "have not yet applied:\n"
    "  1. UPSCALE the image ~3-4x (e.g. PIL `img.resize((w*3, h*3), "
    "Image.LANCZOS)`) — this alone usually fixes small/low-DPI images.\n"
    "  2. Pass an EXPLICIT dpi to tesseract: `--dpi 300` (or `tesseract ... "
    "-c user_defined_dpi=300`).\n"
    "  3. Convert to grayscale and binarize (threshold) so glyph edges are "
    "crisp; try `--psm 6` for a uniform block of text.\n"
    "  4. Re-run and DIFF the new text against this one; treat characters that "
    "change between runs as low-confidence and disambiguate visually "
    "(common confusions: I/l/1, 0/O, 5/S, 8/B, rn/m, and dropped/added spaces).\n"
    "Verify the extracted text against any sample input/expected output the task "
    "provides before committing downstream code."
)


class OcrQualityAdvisor(MultiHookProcessor):
    """One-time nudge toward high-accuracy tesseract preprocessing on low-DPI OCR."""

    _singleton_group = "ocr_quality_advisor"
    _order = 32  # after CustomEditToolProcessor(30), before CustomSelfVerifyProcessor(90)

    def __init__(self) -> None:
        self._ocr_calls: set[str] = set()
        self._fired: bool = False

    async def on_task_start(self, event):
        self._ocr_calls.clear()
        self._fired = False
        yield event

    async def on_before_tool(self, event):
        if event.tool_name == "Bash":
            command = (event.tool_input or {}).get("command", "") or ""
            if _OCR_INVOKE_RE.search(command):
                self._ocr_calls.add(event.tool_call_id)
        yield event

    async def on_after_tool(self, event):
        if self._fired:
            self._ocr_calls.discard(event.tool_call_id)
            yield event
            return

        was_ocr = event.tool_call_id in self._ocr_calls
        self._ocr_calls.discard(event.tool_call_id)

        result_text = event.result or ""
        if was_ocr and _LOW_RES_SIGNAL_RE.search(result_text):
            self._fired = True
            yield dataclasses.replace(event, result=result_text + _ADVISORY)
        else:
            yield event

    async def on_task_end(self, event):
        self._ocr_calls.clear()
        self._fired = False
        yield event
