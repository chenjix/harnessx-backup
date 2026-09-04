# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""OcrRobustnessReminderProcessor.

Closes a recurring failure mode on tmax / terminal-bench-2 tasks that require
reading text out of an image with OCR (`tesseract`): the agent runs a weak,
default `tesseract` invocation, gets garbled output, retries a couple of
cosmetic PIL contrast/sharpen tweaks that do NOT change the output, and then
gives up on the source entirely — falling back to *guessing* the answer from a
provided sample file. Because the verifier grades against the ground truth that
was only legible in the image, the guessed answer scores 0.

Observed cluster (agent phase, keyed off the agent's own Bash activity, never
off task identifiers):
  - a URL->schema migration task where the schema mapping lived in a low-DPI
    synthetic PNG; the agent's contrast-only tweaks never cleared the garble,
    so it invented output keys from the sample's query-parameter names and
    scored 0.0 accuracy against the golden schema.
  - a forensic task where an SSH public key had to be transcribed from an image;
    a misread key means the detector never matches and every trojan bypasses.

The shared root cause is a *strategy* gap, not a capability gap: the OCR tool is
present and works, the model simply does not know the standard OCR-recovery
ladder for clean synthetic images. The single highest-impact lever it never
pulled is UPSCALING (tesseract itself warned "Invalid resolution 0 dpi. Using
70 instead" — a strong signal the input is far below tesseract's ~300-dpi sweet
spot), followed by `--dpi`, sweeping `--psm` page-segmentation modes, and simple
binarization. Cosmetic contrast/sharpen alone rarely moves a low-resolution
render.

This processor supplies that missing strategy as a *mechanical, one-shot* nudge:
the first time the agent has demonstrably run OCR on an image, it injects a
task-agnostic OCR-robustness checklist on the next model turn (before the agent
has a chance to give up and guess). It fires at most once per task, injects a
single user message via the proven keepalive-tool-call path, changes no control
flow, and is harmless on tasks that do not touch images (it simply never arms).
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolCallEvent,
)
from harnessx.core.processor import MultiHookProcessor


# Signals in the agent's Bash commands that indicate it is doing OCR on an
# image. Intentionally broad but anchored on OCR intent (a bare `.png` mention
# is not enough — it must co-occur with an OCR tool) so it never arms on tasks
# that merely reference an image file for some non-OCR reason.
_OCR_TOOL_RE = re.compile(
    r"""
    (?:\btesseract\b)          # the tesseract CLI
    | (?:\bpytesseract\b)      # the python binding
    | (?:image_to_string)      # pytesseract API call
    | (?:\bocrmypdf\b)         # pdf OCR wrapper
    | (?:\bgocr\b|\bocrad\b)   # alternative OCR engines
    """,
    re.IGNORECASE | re.VERBOSE,
)

_OCR_MSG = """\
[OcrRobustnessCheck] You are extracting text from an image with OCR, and the \
correctness of the whole task depends on transcribing that text accurately. \
Default / cosmetic OCR runs (a bare `tesseract in out`, or only tweaking \
contrast/brightness/sharpness) frequently return garbled text on clean \
synthetic renders and screenshots. Before you trust any OCR output — and \
BEFORE you fall back to guessing from a sample file — work the standard \
OCR-recovery ladder and stop as soon as the text is clean and internally \
consistent:

1. UPSCALE the image first. This is usually the single biggest win for \
   low-resolution renders (if OCR warned about low/`0 dpi`, this is almost \
   certainly the fix). Resize 3-4x with a good resampling filter, e.g. \
   `Image.open(p).resize((w*3, h*3), Image.LANCZOS)`, then OCR the enlarged copy.
2. Tell tesseract the resolution: add `--dpi 300` (and consider `-l eng`).
3. Sweep page-segmentation modes: try several `--psm` values (e.g. 3, 4, 6, \
   11, 12) and keep the cleanest result — the default mode often mis-segments \
   tables, lists, and single blocks of text.
4. Binarize / grayscale + threshold (Otsu-style) can help, but apply it AFTER \
   upscaling, not instead of it. Avoid aggressive char whitelists that can \
   silently drop needed symbols.
5. Sanity-check the transcription: re-read it, confirm it is plausible and \
   internally consistent, and cross-check it against any structure you can see \
   (line count, expected token shapes, any sample provided).

Crucially, DERIVE the required output from the AUTHORITATIVE SOURCE (the image \
you just OCR'd), not from the incidental values in a provided sample. If a \
mapping/schema is shown as `left -> right`, the output field names come from \
the RIGHT-hand (target) side of the mapping, not from the input's own \
parameter names. A sample input is only for smoke-testing that your parser \
runs — it does not define the target contract.

This is guidance, not a task step: skip any rung that is clearly unnecessary \
once the text is already clean."""


class OcrRobustnessReminderProcessor(MultiHookProcessor):
    """One-shot OCR-robustness strategy nudge, keyed off the agent's OCR usage.

    Fires at most once per task, and only after the agent's Bash activity has
    matched an OCR-tool signal. The checklist is queued the moment the first OCR
    command is observed and delivered on the *next* model turn by appending a
    single user message in ``on_before_model``. This lands the guidance early
    (right after the first OCR attempt returns, before the agent has a chance to
    give up on the image and guess from a sample) and — because it only *appends*
    a message and never rewrites the model's tool calls — it can never clobber a
    legitimate or foreign-processor tool call on that turn.

    Task-agnostic: it simply never arms on a task that does not run OCR.
    """

    _singleton_group = "ocr_robustness_reminder"
    # Run late in the pipeline (after the other exit/keepalive processors) so
    # our appended message serializes cleanly after any of theirs.
    _order = 92

    def __init__(self) -> None:
        self._ocr_seen = False
        self._reminded = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._ocr_seen = False
        self._reminded = False
        self._pending_message = ""
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "") or ""
            if _OCR_TOOL_RE.search(cmd) and not self._reminded:
                # Queue the checklist to be appended before the next model turn.
                self._ocr_seen = True
                if not self._pending_message:
                    self._pending_message = _OCR_MSG
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_message:
            yield event
            return
        msg = self._pending_message
        self._pending_message = ""
        self._reminded = True
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=msg),),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._ocr_seen = False
        self._reminded = False
        self._pending_message = ""
        yield event
