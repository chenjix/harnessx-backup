# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""RequiredPathAuditProcessor.

Closes the "correct logic, wrong path" structural failure mode of
Terminal-Bench-2 / tmax tasks (documented in the TB2 playbook): the external
verifier checks for output *artifacts at the exact absolute paths named in the
task description*. A deliverable written to a plausible-but-different path or
extension is a hard 0 regardless of content correctness, and the agent — which
cannot see the verifier files — has no feedback loop that surfaces the mismatch.

Concrete observed archetype (task_000010): the task text says the script must
live at ``/home/user/operator.py``; the agent authored a correct-looking script
at ``/home/user/k8s_operator.py`` and finished, so the verifier's
``test_operator_script_exists`` failed on a missing exact path even though the
logic was sound.

Existing exit-time processors on this pipeline only *narrate a text reminder*
("verify your outputs exist"). The model demonstrably skims past such text and
re-declares done. This processor is the mechanical escalation: at the agent's
first exit-intent turn, it extracts the exact absolute output paths named in the
task description and injects a **real** read-only ``Bash`` snapshot that runs
``ls -la`` / ``test -f`` against each of those exact paths. The ground-truth
"path exists / MISSING" listing then lands in context as a tool *result* the
model cannot skim past, followed by one reconcile instruction: if any required
path is MISSING, place the deliverable at the exact required path before
finishing.

This is a *class* fix, not a task fix. It is keyed off generic backtick-quoted
absolute-path syntax in the task text (``/abs/path.ext``), never off task
identifiers, and it never creates, moves, or deletes anything — the injected
command is a read-only existence check. It fires at most once per task and only
when the task text actually names ≥1 exact output path, so tasks without a
stated output-path contract are byte-for-byte unaffected.
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

# Absolute paths quoted in backticks in the task description, e.g.
# `/home/user/operator.py` or `/app/routing_schema.png`. We only take paths
# that look like a concrete file (have a dotted extension of 1-6 word chars) so
# we do not audit directories the agent legitimately populates variably. The
# backtick anchoring keeps this to paths the author explicitly emphasised as a
# contract, not incidental paths mentioned in prose.
_BACKTICK_PATH_RE = re.compile(r"`(/[^`\s]+\.[A-Za-z0-9_]{1,6})`")

# Verbs near a path that mark it as an OUTPUT the agent must PRODUCE (as opposed
# to an INPUT it only reads). Used to prioritise which extracted paths to audit
# when the description names both inputs and outputs. If none of the extracted
# paths carry a produce-verb we fall back to auditing all extracted file paths
# (a missing input is also worth surfacing, and the check is read-only).
_PRODUCE_HINT_RE = re.compile(
    r"\b(?:write|writes|create|creates|save|saves|saved|output|outputs|"
    r"produce|produces|generate|generates|place|placed|store|stored|"
    r"deliver|must\s+live|located\s+at|script\s+at|file\s+at)\b",
    re.IGNORECASE,
)

_AUDIT_TOOL = "Bash"
_AUDIT_MARKER = "[RequiredPathAudit]"

_RECONCILE_MSG = """\
{marker} Before you finish, reconcile your deliverables against the EXACT output \
paths named in the task description. The snapshot above ran a read-only \
existence check on each path the task text quoted. For every path listed as \
MISSING:

- The verifier checks for the artifact at that EXACT absolute path (exact \
directory, exact filename, exact extension) — a file with a similar-but- \
different name or a different extension scores 0 even if its contents are \
correct.
- If you produced the deliverable under a different name/path, move or copy it \
to the exact required path now (do not merely rename in your head).
- If a path is expected to exist and is MISSING, create it at the exact path \
before declaring done.

Do not re-declare done until every required output path in the task shows as \
existing. This check does not verify the artifact's *contents* — keep confirming \
the output is also correct."""


class RequiredPathAuditProcessor(MultiHookProcessor):
    """Actively audit exact required output paths at the agent's exit turn.

    On ``on_task_start`` it parses backtick-quoted absolute file paths out of
    the task description. On the first exit-intent turn (``finish_reason`` in
    ``{end_turn, stop}`` with no tool calls) — and only if ≥1 required path was
    extracted — it rewrites the model turn into a single **real** read-only
    ``Bash`` existence check over those paths, then queues one reconcile
    instruction on the following ``on_before_model``. Bounded to one fire per
    task; never mutates the filesystem; content-agnostic.
    """

    _singleton_group = "tb2_required_path_audit"
    # Run after CustomSelfVerifyProcessor (_order=90) and the svc-deps reminder
    # (_order=91) so the exit-intent hooks serialize — each processor only acts
    # on a turn that still has no tool calls, so at most one converts a given
    # exit attempt into a tool call and the others fire on the next genuine exit.
    _order = 93

    # Cap how many paths we probe so a pathological description cannot build a
    # huge command; the first handful of contract paths are what matter.
    _MAX_PATHS = 12

    def __init__(self) -> None:
        self._required_paths: list[str] = []
        self._audited = False
        self._pending_message = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._required_paths = []
        self._audited = False
        self._pending_message = ""
        desc = event.task_description or ""
        self._required_paths = self._extract_required_paths(desc)
        yield event

    @classmethod
    def _extract_required_paths(cls, desc: str) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        for m in _BACKTICK_PATH_RE.finditer(desc):
            p = m.group(1)
            if p not in seen:
                seen.add(p)
                candidates.append(p)
        if not candidates:
            return []
        # Prefer paths that sit near a produce/output verb (within a small
        # window of the mention) — these are the deliverables the verifier will
        # check for. Fall back to all extracted file paths if none qualify.
        produce_paths: list[str] = []
        for p in candidates:
            idx = desc.find("`" + p + "`")
            if idx == -1:
                continue
            window = desc[max(0, idx - 120): idx + len(p) + 120]
            if _PRODUCE_HINT_RE.search(window):
                produce_paths.append(p)
        chosen = produce_paths if produce_paths else candidates
        return chosen[: cls._MAX_PATHS]

    def _build_audit_command(self) -> str:
        # A single read-only shell one-liner: for each required path, print
        # whether it exists (ls -la) or a clear MISSING line. Never mutates.
        lines = [
            'echo "== Required-output-path existence audit (read-only) =="',
        ]
        for p in self._required_paths:
            safe = p.replace("'", "'\\''")
            lines.append(
                "if [ -e '{q}' ]; then echo 'EXISTS: {p}'; "
                "ls -la '{q}'; else echo 'MISSING: {p}'; fi".format(q=safe, p=p)
            )
        return "\n".join(lines)

    async def on_before_tool(self, event: ToolCallEvent):
        # We do not intercept anything here; the audit is a genuine Bash call
        # executed by the run loop. Pass through untouched.
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
        if exit_intent and self._required_paths and not self._audited:
            self._audited = True
            self._pending_message = _RECONCILE_MSG.format(marker=_AUDIT_MARKER)
            audit_call = ToolCall(
                id=f"pathaudit-{uuid.uuid4().hex[:8]}",
                name=_AUDIT_TOOL,
                input={"command": self._build_audit_command()},
            )
            yield dataclasses.replace(event, tool_calls=(audit_call,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._required_paths = []
        self._audited = False
        self._pending_message = ""
        yield event
