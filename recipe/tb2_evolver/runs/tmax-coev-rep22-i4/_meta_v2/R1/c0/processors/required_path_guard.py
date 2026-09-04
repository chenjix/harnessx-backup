# SPDX-License-Identifier: MIT
"""RequiredPathGuard — block the agent from exiting after it has consciously
substituted a *different* artifact for a task-mandated one.

Structural failure class (see tb2-playbook "Correct logic, wrong path", the
benchmark's #1 structural failure mode):

The task text names an exact required output artifact (a file at an exact
path, an exact filename, an exact directory). The agent hits a self-imposed
tooling obstacle — the mandated name collides with a stdlib module, a chosen
invocation fails, a lib is missing — and, instead of working around the
obstacle, it concludes the requirement is "impossible" or "has to" be done
differently, renames/moves the artifact to a convenient location, and then
declares success. The external verifier checks the *literal* mandated path,
so a fully-correct solution scores 0 purely because the required artifact is
not where the task said it must be.

Concrete evidence from the evolve set (task_000010_644ab1c2): the task
requires a script at ``/home/user/operator.py``. Running it via
``python3 /home/user/operator.py`` puts ``/home/user`` on ``sys.path[0]`` so
the file shadows the stdlib ``operator`` module, crashing on
``import tarfile`` -> ``import shutil`` -> ``import operator``. The agent
diagnosed this correctly but concluded the file "cannot be named operator.py",
permanently renamed it to ``k8s_operator.py``, deleted the required file, and
exited declaring success. The verifier's ``test_operator_script_exists``
(checks the literal ``/home/user/operator.py``) failed. The fix was always
reachable (run from a different CWD, ``python3 -P``, restructure imports) — the
agent conflated "my chosen invocation fails" with "the requirement is
impossible".

Why a Control processor and not a prompt rule: the existing
``CustomSelfVerifyProcessor`` checklist *already* tells the agent to "check
every required output file exists at its exact path", and the agent *ran* it —
then rationalised the missing path away and exited anyway. A passive checklist
does not stop conscious abandonment. This processor is an active, structural
intercept: it only engages when the agent is *exiting* (no tool call) AND its
own just-emitted reasoning contains an explicit requirement-substitution
admission. In that narrow intersection it blocks the exit exactly once and
injects a redirect that reframes the obstacle as workable-around and forbids
substituting a different path/name for a mandated one.

Design / safety:

* Fires on the ``on_after_model`` exit-intent turn, mirroring
  ``CustomSelfVerifyProcessor``'s keepalive-rewrite pattern, using a private
  tool name so it never collides with the self-verify keepalive.
* Ordered *after* both self-verify processors (``_order = 92``). If the
  self-verify processor has already rewritten this exit turn into a keepalive
  (``tool_calls`` non-empty), this turn is no longer an exit-intent for us, so
  we stay silent and only engage on a *later* genuine exit — exactly the
  observed scenario (agent runs self-verify, then still exits with
  abandonment reasoning).
* Fires **at most once per task** (``_fired``) so it can never trap the agent
  in a loop. If the agent exits a second time (with or without the same
  reasoning) it is allowed through.
* Carries **no task-specific literals** — no paths, filenames, UUIDs, or task
  IDs. It matches only on generic English abandonment phrasing, so it
  generalises to any task with an exact mandated artifact.
* Only *appends* one user message and rewrites the exiting turn's tool_calls
  to a single synthetic keepalive (never approved, synthetic result) — it
  never mutates the system prompt and never drops existing messages.
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

_GUARD_TOOL = "_tb2_required_path_guard"
_GUARD_ACK = "Requirement check initiated. See the message above."

# Generic English signals that the agent is *substituting* a different artifact
# for a task-mandated one, or declaring a mandated requirement impossible.
# Deliberately phrase-based (no task literals) so it generalises. Two families:
#   (a) explicit substitution ("renamed ... to", "used a different name/path",
#       "instead of", "had to name/call/save it")
#   (b) impossibility rationalisation about a required artifact
#       ("cannot be named", "impossible to ... at that path", "conflict").
_ABANDON_SUBSTITUTION = re.compile(
    r"""(?ix)
    (?:
        \b(?:had|have)\ to\ (?:rename|name|call|save|place|put|use)\b
      | \brenamed?\ (?:it|the\ (?:file|script))\b
      | \b(?:used|chose|picked|saved|wrote|created)\ (?:it\ )?
            (?:as\ )?a?\ ?(?:different|alternate|another)\ (?:name|path|file|location|filename)\b
      | \binstead\ of\ (?:the\ )?(?:required|requested|specified|mandated|exact)\b
      | \bcould\ ?n[o']t\ be\ named\b
      | \bcannot\ be\ named\b
      | \bcan['’]t\ be\ named\b
      | \b(?:impossible|not\ possible)\ to\ (?:name|create|place|save|write)\b
      | \b(?:technically\ )?impossible\ (?:due\ to|because)\b
      | \bdue\ to\ (?:a\ |the\ )?(?:python\ )?(?:module|name|naming|import)\ conflict\b
    )
    """
)

_REQUIRED_PATH_REDIRECT = (
    "[RequiredArtifactGuard] Your final message indicates you satisfied a "
    "task requirement with a DIFFERENT path, filename, or location than the "
    "one the task explicitly mandated (e.g. you renamed the artifact, used an "
    "alternate name, or concluded the required name/path is impossible).\n\n"
    "This almost always scores 0: the grader checks the LITERAL path/name from "
    "the task description, not an equivalent artifact you produced elsewhere. "
    "A file that runs correctly under a different name is still a failure if "
    "the mandated file does not exist at its exact path.\n\n"
    "The obstacle you hit is almost never a real impossibility — it is a "
    "solvable tooling detail. Common general fixes:\n"
    "  - A filename that shadows a stdlib/module import only breaks when its "
    "own directory is first on sys.path. Run it from a different working "
    "directory (`cd /tmp && python3 /full/path/to/script.py`), use "
    "`python3 -P` / `PYTHONSAFEPATH=1`, or restructure so the top level does "
    "not import the shadowed module.\n"
    "  - A permission/location obstacle: create the artifact where mandated, "
    "adjusting how you build/copy it rather than moving the target.\n\n"
    "Do NOT substitute a different path or name. Make the EXACT mandated "
    "artifact exist at its EXACT path, verify it with `ls -l <exact_path>`, "
    "then confirm the required behaviour still works. Only exit once the "
    "literal requirement is satisfied."
)


class RequiredPathGuard(MultiHookProcessor):
    """Block one exit where the agent admits substituting a mandated artifact."""

    _singleton_group = "tb2_required_path_guard"
    _order = 92  # after CustomSelfVerifyProcessor (90) and HttpVerifierDep (91)

    def __init__(self) -> None:
        self._fired = False
        self._pending_message: str = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._pending_message = ""
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
        # Only engage on a genuine exit-intent turn: model wants to stop and
        # issued no tool call. If another processor already rewrote this turn
        # into a keepalive (tool_calls present), it is no longer an exit for us.
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if not exit_intent or self._fired:
            yield event
            return

        text = f"{event.content or ''}\n{event.thinking or ''}"
        if not _ABANDON_SUBSTITUTION.search(text):
            yield event
            return

        # Block this exit exactly once: convert it into a synthetic keepalive
        # tool call and queue the redirect for the next model turn.
        self._fired = True
        self._pending_message = _REQUIRED_PATH_REDIRECT
        keepalive = ToolCall(
            id=f"rpg-{uuid.uuid4().hex[:8]}",
            name=_GUARD_TOOL,
            input={},
        )
        yield dataclasses.replace(event, tool_calls=(keepalive,))

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name == _GUARD_TOOL:
            yield dataclasses.replace(
                event, approved=False, synthetic_result=_GUARD_ACK
            )
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._pending_message = ""
        yield event
