# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""VerifierHttpDepReminder — surface the external-HTTP-verifier dependency gap.

Closes a systemic, structural failure mode observed across the Tmax evolve
set: a whole cluster of tasks ask the agent to stand up a *service* (an HTTP
server behind nginx, a UNIX-socket HTTP backend, a Python/C++/Rust web
microservice) that an **external automated verifier** then exercises by
making HTTP requests against it. In the TB2/Tmax sandbox the verifier runs
in a *separate phase* against the container's final state, using the
container's system Python — and its ``test_final_state.py`` almost always
does ``import requests``.

The base images in this task family ship without the ``requests`` package,
so the verifier's test module fails to even *collect*::

    ImportError while importing test module '/tmp/test_final_state.py'.
    E   ModuleNotFoundError: No module named 'requests'
    Interrupted: 1 error during collection

This is a hard, reward=0 failure *regardless of whether the agent's service
is correct* — e.g. one observed trajectory served ``HTTP/1.1 200 OK`` with
the right body through nginx and still scored 0 purely because the verifier
could not import ``requests``.

Why the agent never fixes it on its own:
  * the verifier test files do not exist during the agent phase, so the
    agent cannot see that ``import requests`` is required (a structural fact
    of the sandbox topology — see the tb2-playbook);
  * ``requests`` is not needed by the agent's *own* work (it tests its
    service with ``curl`` / ``/dev/tcp``), so it has no local signal to
    install it;
  * outbound ``pip install`` *does* succeed in this environment (observed:
    numpy/scipy/maturin all pulled from PyPI), so the fix is available — the
    agent just doesn't know to apply it.

This processor injects ONE runtime reminder, on the first step, only when the
task description signals that an external HTTP verifier will probe a service
the agent must build. The reminder tells the agent to ensure the standard
Python HTTP client library is importable in the *system* interpreter before
finishing, so the external verifier's test module can be collected. The
*action* stays agent-authored (the agent runs the install via Bash); the
processor only delivers the missing knowledge at the right moment.

Generalisation: the trigger keys on generic service/verifier phrasing
("automated verifier", "make HTTP requests", "HTTP server/service/endpoint",
"listen on ... :PORT", nginx/uvicorn/gunicorn/proxy_pass, UNIX-socket
servers), not on any task id, path, or constant. It fires for any
build-a-service-the-verifier-calls task and is a no-op for pure
file/data/query tasks that no external HTTP verifier touches.
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

# Two independent signal groups. We require (verifier-intent) AND (service-shape)
# so we only fire on "build a service that an external verifier will call over
# HTTP" tasks, never on generic scripting/data tasks.
_VERIFIER_RE = re.compile(
    r"automated\s+verifier|external\s+verifier|\bverifier\s+will\b|"
    r"will\s+(?:make|send|issue)\s+(?:http|get|post|requests?)|"
    r"grader\s+will|test\s+harness\s+will",
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
    "that an EXTERNAL automated verifier will exercise by making HTTP "
    "requests. That verifier runs AFTER you stop, in a separate phase, using "
    "this container's system Python interpreter — and its test module "
    "typically does `import requests`. If the `requests` package is not "
    "importable in the system Python, the verifier's tests fail to even load "
    "(ModuleNotFoundError during collection) and the task scores zero even if "
    "your service is perfectly correct.\n"
    "Before you finish, make the final container state robust for that "
    "verifier:\n"
    "  1. Verify the standard Python HTTP client is importable in the SYSTEM "
    "interpreter, e.g. `python3 -c 'import requests'` (outbound pip installs "
    "do work here — `pip3 install requests` if it is missing). Install it "
    "system-wide, NOT inside a project virtualenv the verifier won't use.\n"
    "  2. Make sure your service is actually listening and reachable at the "
    "exact host:port / socket path stated in the task, and that any "
    "background process you launched stays alive after you exit.\n"
    "This is about environment robustness, not the task's core logic — spend "
    "one command on it, then continue solving the task."
)


class VerifierHttpDepReminder(MultiHookProcessor):
    """Inject a one-time reminder about the external HTTP verifier's Python deps.

    Fires at most once per task, on the first step whose accumulated context
    matches BOTH an external-verifier signal AND a build-a-service signal in
    the task description. Contract-safe: appends exactly one ``user`` message
    to ``event.messages`` / ``event.raw_messages`` on the single step it fires.

    Parameters
    ----------
    dep_module:
        The client library name surfaced in the reminder (default ``requests``
        — the module the observed verifier test modules import). Kept as a
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
        return bool(_VERIFIER_RE.search(text) and _SERVICE_RE.search(text))

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
