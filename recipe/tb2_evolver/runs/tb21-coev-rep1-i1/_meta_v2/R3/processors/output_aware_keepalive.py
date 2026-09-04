# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""OutputAwareKeepalive — repeatable, bounded keepalive whose continuation nudge
names the *declared output artifact path(s)* the current task asked the agent to
produce.

Why this exists (harness deficiency, not a model-knowledge gap)
---------------------------------------------------------------
On Terminal-Bench 2 the only tool is ``Bash`` and a task is solved by *writing
files via Bash*. The run loop treats any assistant turn with
``finish_reason in ("end_turn","stop")`` and no tool calls as a natural
completion and stops. R2's ``ActLoopKeepalive`` made the keepalive
*repeatable-but-bounded* so a rambling agent gets nudged back to action several
times. That landed: previously-dying tasks now run 20-100 Bash steps.

The R2 round then surfaced the *next* dominant failure signature: even after
running many steps, the required output file the task **explicitly names** is
never written to its exact path. Six of R2's failing tasks fail the verifier's
*first* precondition — "output file exists" — with a ``FileNotFoundError`` on a
``/app/<X>`` path that the task description told the agent to create. In the
episode logs the agent visibly loses track of the deliverable: on regex-log it
rambled 113k chars about the regex (having cited ``/app/regex.txt`` 14 times)
yet never wrote it; on feal it never referenced the required ``/app/plaintexts.txt``
at all.

The generic R2 nudge ("act via Bash, verify outputs exist") did not close this
cluster — the missing piece is *dynamic, per-task context*: the specific output
path lives in the task description and differs every task, and the agent stops
tracking it mid-run. A Control hook can extract that path once at task start and
re-surface it at the decisive terminal-stall step; a static system-prompt rule
cannot (it can only speak generically, which R2 already did).

Design: strict superset of R2's ``ActLoopKeepalive``
----------------------------------------------------
This processor keeps R2's exact contract (repeatable bounded keepalive: inject a
synthetic keepalive tool call so the loop does not ``break``; queue one user
nudge delivered on the next ``on_before_model``; swallow the synthetic tool call
in ``on_before_tool``; honour a completion sentinel escape hatch; exhaust budget
→ yield unchanged so the loop stops). It replaces R2 one-for-one in the pipeline
so there is exactly one stall-interceptor and the +1 message-insertion contract
is preserved.

The ONLY behavioural addition: on ``on_task_start`` it parses the task
description for output paths that appear adjacent to a produce-verb
(save/write/create/output/produce/store/generate/place/put/emit). When a stall
fires and such paths were found, the queued nudge names them specifically
("You have not confirmed these required output files exist yet: /app/regex.txt.
Write your current best result to that exact path via Bash now, then verify with
`ls -l`."). When no path is extracted the nudge degrades to R2's generic text —
so this is never worse than R2.

No task-specific literals: the produce-verb list and the ``/app/...`` /
absolute-path shapes are generic; the concrete paths are read from the live
``task_description`` at runtime, never hardcoded.
"""

from __future__ import annotations

import dataclasses
import re
import uuid

from harnessx.core.events import (
    ModelResponseEvent,
    ToolCallEvent,
    TaskStartEvent,
    TaskEndEvent,
    BeforeModelEvent,
    Message,
    ToolCall,
)
from harnessx.core.processor import MultiHookProcessor

_KEEPALIVE_TOOL = "_tb2_out_keepalive"
_KEEPALIVE_ACK = "Continuation acknowledged. See the message below and keep working."

# Verbs that, when they precede a path in the task text, mark that path as an
# output the agent is expected to produce (as opposed to an input to read).
_PRODUCE_VERBS = (
    "save",
    "write",
    "writes",
    "create",
    "creates",
    "output",
    "outputs",
    "produce",
    "produces",
    "store",
    "stores",
    "generate",
    "generates",
    "place",
    "put",
    "emit",
    "result in",
    "results in",
)

# Absolute POSIX path, conservative charset. Trailing punctuation is stripped
# separately so we don't capture a sentence-ending period as part of the path.
_PATH_RE = re.compile(r"(/[A-Za-z0-9_][A-Za-z0-9_./+-]*)")
# A path is treated as an output only if a produce-verb occurs within this many
# characters before it. Keeps "read /app/input.csv" from being flagged. Kept
# tight so input paths mentioned in a nearby but distinct clause are not swept in.
_VERB_WINDOW = 40

_GENERIC_NUDGE = (
    "You stopped without running a command, but the task is not finished — on "
    "this environment work only counts when it is performed through the Bash "
    "tool (writing files, running scripts, starting services). Planning or "
    "explaining in prose does not change the filesystem.\n\n"
    "Do ONE of the following now:\n"
    "  1. If there is still work to do, take the next concrete step by calling "
    "the Bash tool with an actual command (create/inspect the required output "
    "file, run your script, verify a service is up, etc.).\n"
    "  2. If — and only if — you have already verified with `ls` and `cat` that "
    "every required output file exists at its exact path and its contents are "
    "correct, end your message with the line `SUCCESS: task complete.` to "
    "finish.\n\n"
    "Do not repeat this reasoning back to me; act."
)


def _extract_output_paths(task_description: str) -> tuple[str, ...]:
    """Return declared output file paths mentioned near a produce-verb.

    Conservative: an absolute path counts as an output only when one of
    ``_PRODUCE_VERBS`` appears within ``_VERB_WINDOW`` chars before it. Paths
    that look like directories (trailing ``/``) or that carry no filename dot
    but sit far from any verb are dropped. Deduplicated, order-preserving.
    """
    text = task_description or ""
    if not text:
        return ()
    lowered = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for m in _PATH_RE.finditer(text):
        raw = m.group(1)
        # Strip trailing sentence punctuation the charset may have swept up.
        path = raw.rstrip(".,;:)]}'\"")
        if len(path) < 3 or path.endswith("/"):
            continue
        start = m.start(1)
        window = lowered[max(0, start - _VERB_WINDOW) : start]
        if not any(v in window for v in _PRODUCE_VERBS):
            continue
        if path in seen:
            continue
        seen.add(path)
        found.append(path)
    return tuple(found)


def _targeted_nudge(paths: tuple[str, ...]) -> str:
    listed = ", ".join(paths)
    return (
        "You stopped without running a command, but the task is NOT finished. "
        "The task asked you to produce the following output file(s) and you "
        f"have not confirmed they exist yet: {listed}.\n\n"
        "On this environment prose does not change the filesystem — only Bash "
        "commands do. Do this now:\n"
        f"  1. If you already worked out the answer/content, write it to the "
        f"EXACT path(s) above via Bash immediately (e.g. a here-doc or "
        f"redirect), then run `ls -l` on each to confirm it exists and is "
        f"non-empty.\n"
        "  2. If a required output is still not produced, take the next "
        "concrete Bash step toward creating it.\n"
        "  3. Only after you have verified with `ls`/`cat` that every required "
        "output file exists at its exact path with correct contents, end your "
        "message with the line `SUCCESS: task complete.` to finish.\n\n"
        "Do not repeat this reasoning back to me; act via Bash."
    )


class OutputAwareKeepalive(MultiHookProcessor):
    """Repeatable, bounded keepalive whose nudge names the task's declared
    output artifact path(s). Strict superset of R2's ActLoopKeepalive.

    Args:
        max_reprompts: Maximum number of continuation nudges injected per task.
            Each fires on a distinct no-tool-call stall turn. After the budget
            is spent the run loop is allowed to stop normally.
        completion_marker: Case-insensitive substring that, when present in the
            stalling turn's content, is treated as a deliberate completion — the
            agent is allowed to stop instead of being nudged again.
        min_content_chars: Only treat a no-tool-call turn as a rambling stall
            worth reprompting when its content is at least this long.
    """

    required_providers: frozenset = frozenset()

    _singleton_group = "tb2_output_aware_keepalive"
    # Same slot R2's keepalive occupied: just after the stock
    # CustomSelfVerifyProcessor (_order=90), so on the turn that one-shot handles
    # the response already carries its keepalive tool call and this stays silent.
    _order = 91

    def __init__(
        self,
        max_reprompts: int = 6,
        completion_marker: str = "SUCCESS",
        min_content_chars: int = 1,
    ) -> None:
        self.max_reprompts = max(0, int(max_reprompts))
        self.completion_marker = str(completion_marker).strip().lower()
        self.min_content_chars = max(0, int(min_content_chars))
        # Keyed by run_id so parallel workers sharing the singleton each track
        # their own state independently.
        self._counts: dict[str, int] = {}
        self._pending: dict[str, str] = {}
        self._out_paths: dict[str, tuple[str, ...]] = {}

    # -- lifecycle -------------------------------------------------------

    async def on_task_start(self, event: TaskStartEvent):
        self._counts.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        self._out_paths[event.run_id] = _extract_output_paths(
            getattr(event, "task_description", "") or ""
        )
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._counts.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        self._out_paths.pop(event.run_id, None)
        yield event

    # -- inject queued nudge --------------------------------------------

    async def on_before_model(self, event: BeforeModelEvent):
        msg = self._pending.pop(event.run_id, "")
        if not msg:
            yield event
            return
        # Prior turn ended with the synthetic keepalive tool result (role tool),
        # so appending exactly one user message satisfies the +1 contract and
        # leaves the window ending on "user".
        yield dataclasses.replace(
            event,
            messages=tuple(event.messages) + (Message(role="user", content=msg),),
        )

    # -- detect stall ----------------------------------------------------

    async def on_after_model(self, event: ModelResponseEvent):
        run_id = event.run_id
        stall = (
            event.finish_reason in ("end_turn", "stop")
            and not event.tool_calls
            and bool((event.content or "").strip())
            and len(event.content or "") >= self.min_content_chars
        )
        if not stall:
            yield event
            return

        # Deliberate completion — let it stop.
        if self.completion_marker and self.completion_marker in (event.content or "").lower():
            yield event
            return

        count = self._counts.get(run_id, 0)
        if count >= self.max_reprompts:
            # Budget exhausted — allow the run loop to stop naturally.
            yield event
            return

        self._counts[run_id] = count + 1
        paths = self._out_paths.get(run_id, ())
        self._pending[run_id] = _targeted_nudge(paths) if paths else _GENERIC_NUDGE
        keepalive = ToolCall(
            id=f"ok-{uuid.uuid4().hex[:8]}",
            name=_KEEPALIVE_TOOL,
            input={},
        )
        yield dataclasses.replace(event, tool_calls=(keepalive,))

    # -- swallow the synthetic keepalive tool call ----------------------

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _KEEPALIVE_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_KEEPALIVE_ACK
            )
        else:
            yield event
