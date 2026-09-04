# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""VerifierHttpDepReminder — surface the external-HTTP-verifier dependency gap.

Closes a systemic, structural failure mode observed across the Tmax evolve
set: a whole cluster of tasks ask the agent to stand up a *service* (an HTTP
server behind nginx, a UNIX-socket HTTP backend, a Python/C++/Rust web
microservice) that an **external automated verifier / integration test** then
exercises by making HTTP requests against it. In the TB2/Tmax sandbox the
verifier runs in a *separate phase* against the container's final state, using
the container's system Python — and its ``test_final_state.py`` almost always
does ``import requests``.

The base images in this task family ship without the ``requests`` package,
so the verifier's test module fails to even *collect*::

    ImportError while importing test module '/tmp/test_final_state.py'.
    E   ModuleNotFoundError: No module named 'requests'
    Interrupted: 1 error during collection

This is a hard, reward=0 failure *regardless of whether the agent's service
is correct* — e.g. observed trajectories served correct HTTP responses (Flask
API, C++ microservice, Rust-extension service) and still scored 0 purely
because the verifier could not import ``requests``.

Why the agent never fixes it on its own:
  * the verifier test files do not exist during the agent phase, so the
    agent cannot see that ``import requests`` is required (a structural fact
    of the sandbox topology — see the tb2-playbook);
  * ``requests`` is not needed by the agent's *own* work (it tests its
    service with ``curl`` / ``/dev/tcp``), so it has no local signal to
    install it — and when the agent's service is C++/Rust it may never touch
    Python at all;
  * outbound ``pip install`` *does* succeed in this environment (observed:
    numpy/scipy/maturin all pulled from PyPI), so the fix is available — the
    agent just doesn't know to apply it.

This processor injects ONE runtime reminder, on the first step, only when the
task description signals that an external HTTP prober will exercise a service
the agent must build and then leave running. The reminder tells the agent to
ensure the standard Python HTTP client library is importable in the *system*
interpreter before finishing, so the external verifier's test module can be
collected. The *action* stays agent-authored (the agent runs the install via
Bash); the processor only delivers the missing knowledge at the right moment.

Generalisation & trigger widening (R2)
--------------------------------------
The original R1 trigger required explicit *verifier* vocabulary
("automated verifier", "verifier will make requests", ...). Trajectory
review showed the same reward=0 ``ModuleNotFoundError: requests`` failure on
tasks that never use that vocabulary — they instead say the service will be
hit by an *"automated integration test"*, an *"automated test [that] can send
HTTP requests"*, or simply ask the agent to *"leave the server running so our
tests can query it"*. The R1 regex fired on **0 of 4** observed reqfail tasks.

The generalisable structural signal is: (a) the task asks for a real network
service (``_SERVICE_RE`` — an HTTP/web server bound to a host:port or socket),
AND (b) an *external prober will hit it after the agent exits* — expressed by
any of a wide family of phrasings (verifier / grader / integration test /
automated test / "our tests" / "will|can query|send|hit|probe|exercise" /
"leave it running" / "accept traffic" / "runs continuously" / "running in the
background"). Pairing the widened prober signal with the already-strict
service-shape signal keeps the reminder a no-op for pure file/data/query tasks
that no external HTTP prober touches, while now catching the whole
build-a-service-and-leave-it-running cluster. Still keyed on generic phrasing
only — no task id, path, port, or constant.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    Message,
    StepStartEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

# Two independent signal groups. We require (prober-intent) AND (service-shape)
# so we only fire on "build a service that an external prober will call over
# HTTP and leave running" tasks, never on generic scripting/data tasks.
#
# _PROBER_RE (widened in R2): any phrasing that means "something outside the
# agent's own session will exercise this service after the agent stops".
_PROBER_RE = re.compile(
    r"automated\s+verifier|external\s+verifier|\bverifier\b|"
    r"integration\s+test|automated\s+test|our\s+(?:automated\s+)?tests?\b|"
    r"test\s+(?:can|will|harness|suite|script)\b|"
    r"(?:will|can)\s+(?:make|send|issue|query|probe|hit|call|exercise)\b|"
    r"\bgrader\b|verify\s+your\s+work|"
    r"leave\s+(?:the|your|it)\b.{0,40}\brunning\b|"
    r"keep\s+(?:the|your|it)\b.{0,40}\brunning\b|"
    r"runs?\s+continuously|accept\s+traffic|"
    r"running\s+in\s+the\s+(?:back|fore)ground",
    re.IGNORECASE,
)
_SERVICE_RE = re.compile(
    r"\bhttp\s+(?:server|service|endpoint|api)\b|\bweb\s+(?:server|service|api)\b|"
    r"microservice|reverse\s+proxy|proxy_pass|\bnginx\b|\buvicorn\b|\bgunicorn\b|"
    r"\bflask\b|\bfastapi\b|\bunix\s+socket\b|\bsock\b|"
    r"listen(?:ing)?\s+on\b|127\.0\.0\.1:|localhost:\d",
    re.IGNORECASE,
)

_REMINDER = (
    "[Environment / verifier note] This task asks you to stand up a service "
    "and leave it running so that an EXTERNAL automated prober (an integration "
    "test / verifier / grader) can exercise it by making HTTP requests. That "
    "prober runs AFTER you stop, in a separate phase, using this container's "
    "system Python interpreter — and its test module typically does "
    "`import requests`. If the `requests` package is not importable in the "
    "system Python, the prober's tests fail to even load (ModuleNotFoundError "
    "during collection) and the task scores zero even if your service is "
    "perfectly correct. This applies even when your own service is written in "
    "C++, Rust, or another language — the prober is still Python.\n"
    "Before you finish, make the final container state robust for that prober:\n"
    "  1. Verify the standard Python HTTP client is importable in the SYSTEM "
    "interpreter, e.g. `python3 -c 'import requests'` (outbound pip installs "
    "do work here — `pip3 install requests` if it is missing). Install it "
    "system-wide, NOT inside a project virtualenv the prober won't use.\n"
    "  2. Make sure your service is actually listening and reachable at the "
    "exact host:port / socket path stated in the task, and that any "
    "background process you launched stays alive after you exit (do not kill "
    "it in your final command).\n"
    "This is about environment robustness, not the task's core logic — spend "
    "one command on it, then continue solving the task."
)


class VerifierHttpDepReminder(MultiHookProcessor):
    """Inject a one-time reminder about the external HTTP prober's Python deps.

    Fires at most once per task, on the first step whose task description
    matches BOTH an external-prober signal AND a build-a-service signal.
    Contract-safe: appends exactly one ``user`` message to
    ``event.messages`` / ``event.raw_messages`` on the single step it fires.

    Parameters
    ----------
    dep_module:
        The client library name surfaced in the reminder (default ``requests``
        — the module the observed prober test modules import). Kept as a
        knob so future rounds can widen it without editing code.
    """

    _singleton_group = "verifier_http_dep_reminder"
    _order = 7  # after TaskTimeReminder (6), before compaction/self-verify

    def __init__(self, dep_module: str = "requests") -> None:
        self.dep_module = str(dep_module)
        self._fired = False
        self._task_text = ""

    async def on_task_start(self, event: TaskStartEvent):
        self._fired = False
        self._task_text = event.task_description or ""
        yield event

    def _matches(self, text: str) -> bool:
        return bool(_PROBER_RE.search(text) and _SERVICE_RE.search(text))

    async def on_step_start(self, event: StepStartEvent):
        if self._fired:
            yield event
            return

        # Prefer the task description captured at task start; fall back to the
        # first user message visible in the current context if it was empty.
        text = self._task_text
        if not text and event.messages:
            for m in event.messages:
                role = getattr(m, "role", None)
                if role == "user":
                    text = getattr(m, "content", "") or ""
                    break

        if not text or not self._matches(text):
            yield event
            return

        self._fired = True
        msg = Message(role="user", content=_REMINDER)
        yield dataclasses.replace(
            event,
            messages=event.messages + (msg,),
            raw_messages=event.raw_messages + (msg,),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._fired = False
        self._task_text = ""
        yield event
