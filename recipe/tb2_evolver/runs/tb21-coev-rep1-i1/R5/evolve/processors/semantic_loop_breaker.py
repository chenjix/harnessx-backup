# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""SemanticLoopBreaker — break an agent out of a repeated-reasoning loop.

Motivation (harness deficiency, not a model-knowledge gap)
----------------------------------------------------------
On Terminal-Bench 2 the R1 context-overflow guard and R2 act-loop keepalive
removed the "instant death" and "single prose stall" terminal failure shapes:
every task now runs many Bash steps. A new dominant failure signature emerged
in the R4 trajectories — a **semantic loop**. The agent emits the *same
reasoning content repeatedly* (near-identical assistant turns), re-running the
same class of command with tiny variations, making no forward progress, until
it exhausts ``max_steps`` / the budget and never writes the required output
file.

The stock hash / exact-match loop detection does NOT catch this: the tool-call
IDs and the exact command bytes differ slightly between iterations, so no two
turns are byte-identical even though the agent's *reasoning* is verbatim
identical. The model is frequently self-aware about it — the R4 logs contain
assistant turns literally beginning:

    "I'm stuck in a loop repeating the same command. Let me take ..."   (x41)
    "I keep making the same mistake. Let me take a fundamentally ..."   (x10)
    "Let me try a different approach. The issue is that the sample..."  (x32)
    "Let me analyze the disassembly more carefully. I see that: ..."    (x25)

— yet it cannot break out on its own, and the harness never intervenes.

Empirical separation observed in R4 (max count of an identical assistant-content
prefix within a run):

    PASSING  tasks: <= 2   (configure-git-webserver, git-leak-recovery, kv-store-grpc)
    FAILING  loops: 3..48  (write-compressor 48, vulnerable-secret 41,
                            protein-assembly 39, adaptive-rejection-sampler 32,
                            llm-inference-batching 10, sam-cell-seg 5, train-fasttext 5)

so a repetition threshold in the 3..5 band cleanly separates the healthy
clusters from the stuck ones.

This is a *class* deficiency (a missing mechanical loop-break), not a
domain-knowledge gap: the fix is a Control hook that watches assistant-turn
reasoning signatures, and once the same signature recurs ``repeat_threshold``
times, injects a single forceful redirect telling the agent to stop repeating,
commit its current best result to the required output path, and change strategy.

Distinction from the R2 keepalive / reverted R3 keepalive
---------------------------------------------------------
Those fired on a *terminal no-tool-call stall* (``finish_reason`` end/stop AND
no tool calls) — a completely different trigger. This processor fires *mid-run
while the agent is still emitting tool calls*, keyed on **content repetition**,
not on the absence of a tool call. It never fabricates a synthetic keepalive
tool call and never blocks the run from ending; it only appends one bounded
user redirect when a loop is detected. Mechanically orthogonal.

Mechanism
---------
``on_after_model`` (``_order=92`` — after the R2 keepalive at 91, so on a turn
the keepalive is handling we still simply record the signature and, because the
keepalive already queued its own nudge, we defer our own to a later step):

  * Compute a *signature* of the assistant turn: a normalised prefix of the
    text content (lowercased, whitespace-collapsed, first ``sig_chars`` chars).
    Turns with empty content are ignored.
  * Push the signature onto a bounded rolling deque per run and count how many
    of the last ``window`` signatures equal the current one.
  * When that count reaches ``repeat_threshold`` AND the per-run intervention
    budget (``max_interventions``) is not spent AND no redirect is already
    pending, queue exactly one redirect message and reset the local repeat
    tally so the next intervention only fires after a *fresh* run of repeats.

``on_before_model`` delivers the queued redirect as exactly one ``user``
message, and only when the prior message role is not ``user`` (contract-safe
+1 insertion). In a loop the prior turn ends on a ``tool`` result, so this
holds. If the last role is already ``user`` (e.g. another processor just added
one) we keep the redirect pending and deliver it next step rather than risk a
+2 chain violation.

Contract notes
--------------
- Only ``on_after_model`` reads the response; it never mutates message history
  from that hook. It does not touch ``tool_calls`` — the agent's own command
  still runs this step; the redirect lands on the *following* model call.
- ``on_before_model`` appends at most one ``user`` message, guarded on the
  last-role-not-user rule, satisfying the +1 insertion contract.
- No task-specific literals: the signature logic and redirect text are generic
  terminal-agent guidance keyed on repetition, applicable to any task.
"""

from __future__ import annotations

import dataclasses
import re
from collections import deque

from harnessx.core.events import (
    ModelResponseEvent,
    TaskStartEvent,
    TaskEndEvent,
    BeforeModelEvent,
    Message,
)
from harnessx.core.processor import MultiHookProcessor

_WS = re.compile(r"\s+")

_REDIRECT_MSG = (
    "STOP. You have repeated essentially the same analysis / command several "
    "times now without making progress — you are in a loop. Repeating the same "
    "reasoning will not change the result.\n\n"
    "Break the loop now by doing BOTH of the following, in order:\n"
    "  1. If the task names an output file you must produce, write your CURRENT "
    "best answer to that exact path via the Bash tool immediately (a partial or "
    "best-effort result is strictly better than no file), then `ls -l` it to "
    "confirm it exists.\n"
    "  2. Then take a GENUINELY DIFFERENT next step — do not re-run the command "
    "you just ran. Change tool, inspect a different file, test a different "
    "hypothesis, or simplify your approach. State in one short line what is new "
    "about this attempt before running it.\n\n"
    "Do not restate your previous reasoning; act differently."
)


class SemanticLoopBreaker(MultiHookProcessor):
    """Detect a repeated-reasoning loop and inject a bounded strategy-change
    redirect that also asks the agent to commit its best current output.

    Args:
        repeat_threshold: How many times the same assistant-content signature
            must appear within the rolling ``window`` before a redirect fires.
            R4 evidence: passing tasks stay <= 2; stuck tasks reach 3..48, so a
            value of 4 sits above the healthy band and below the stuck band.
        window: Size of the rolling signature history examined for repeats.
        sig_chars: Number of leading characters (after normalisation) used to
            form the content signature. Long enough to distinguish distinct
            reasoning, short enough that trivial tail edits still collapse to
            the same signature.
        max_interventions: Per-run cap on how many redirects are injected. Kept
            small so a genuinely-progressing agent that happens to phrase two
            turns similarly is not spammed, and so we never spin forever.
        min_sig_chars: Ignore turns whose normalised content is shorter than
            this (empty / one-word acknowledgements are not loop evidence).
    """

    required_providers: frozenset = frozenset()

    _singleton_group = "tb2_semantic_loop_breaker"
    # After the R2 ActLoopKeepalive (_order=91): on a no-tool-call stall the
    # keepalive handles the turn and queues its own nudge; we only record the
    # signature there and defer our redirect, avoiding a +2 before_model chain.
    _order = 92

    def __init__(
        self,
        repeat_threshold: int = 4,
        window: int = 8,
        sig_chars: int = 80,
        max_interventions: int = 3,
        min_sig_chars: int = 12,
    ) -> None:
        self.repeat_threshold = max(2, int(repeat_threshold))
        self.window = max(self.repeat_threshold, int(window))
        self.sig_chars = max(16, int(sig_chars))
        self.max_interventions = max(0, int(max_interventions))
        self.min_sig_chars = max(0, int(min_sig_chars))
        # Per-run state, keyed by run_id so parallel workers sharing the
        # singleton track independently.
        self._history: dict[str, deque] = {}
        self._interventions: dict[str, int] = {}
        self._pending: dict[str, str] = {}

    # -- helpers ---------------------------------------------------------

    def _signature(self, content: str) -> str:
        norm = _WS.sub(" ", (content or "").strip().lower())
        if len(norm) < self.min_sig_chars:
            return ""
        return norm[: self.sig_chars]

    # -- lifecycle -------------------------------------------------------

    async def on_task_start(self, event: TaskStartEvent):
        self._history.pop(event.run_id, None)
        self._interventions.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._history.pop(event.run_id, None)
        self._interventions.pop(event.run_id, None)
        self._pending.pop(event.run_id, None)
        yield event

    # -- deliver queued redirect ----------------------------------------

    async def on_before_model(self, event: BeforeModelEvent):
        msg = self._pending.get(event.run_id, "")
        if not msg:
            yield event
            return
        msgs = tuple(event.messages)
        # Contract-safe +1: only add when the last role is not already 'user'
        # (avoids a +2 chain if another processor added a user message first).
        if msgs and msgs[-1].role == "user":
            # Keep it pending; deliver on a later step.
            yield event
            return
        self._pending.pop(event.run_id, None)
        yield dataclasses.replace(
            event,
            messages=msgs + (Message(role="user", content=msg),),
        )

    # -- detect repeated-reasoning loop ---------------------------------

    async def on_after_model(self, event: ModelResponseEvent):
        run_id = event.run_id
        sig = self._signature(event.content or "")
        if not sig:
            yield event
            return

        hist = self._history.get(run_id)
        if hist is None:
            hist = deque(maxlen=self.window)
            self._history[run_id] = hist

        # Count occurrences of this signature already in the recent window
        # (before appending the current turn), then append.
        recent_count = sum(1 for s in hist if s == sig)
        hist.append(sig)
        occurrences = recent_count + 1  # including the current turn

        if occurrences < self.repeat_threshold:
            yield event
            return

        # Loop detected. Respect the per-run intervention budget and avoid
        # stacking a second pending redirect.
        used = self._interventions.get(run_id, 0)
        if used >= self.max_interventions or self._pending.get(run_id):
            yield event
            return

        self._interventions[run_id] = used + 1
        self._pending[run_id] = _REDIRECT_MSG
        # Reset the signature history so the next intervention only fires after
        # a fresh run of repeats (prevents firing every subsequent turn).
        hist.clear()
        yield event
