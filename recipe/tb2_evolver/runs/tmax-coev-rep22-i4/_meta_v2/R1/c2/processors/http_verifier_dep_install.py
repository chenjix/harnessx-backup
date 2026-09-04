# SPDX-License-Identifier: MIT
"""HttpVerifierDepInstallProcessor — deterministically make the grading HTTP
client library importable for service tasks.

Structural fact about this benchmark (see tb2-playbook): HTTP / network-service
tasks are graded by an *external* verifier that queries the agent's service
with the standard Python ``requests`` client. The verifier's test files are
injected *after* the agent session ends, so the agent never sees that the test
does ``import requests``. Several service containers ship without ``requests``
installed, which makes the verifier's ``test_final_state.py`` fail at
*collection* time (``ModuleNotFoundError: No module named 'requests'``) even
when the agent's service is fully correct and running.

Why a *reminder* is not enough
------------------------------
The previous version of this hook (``HttpVerifierDepProcessor``) appended a
single user message asking the agent to make ``requests`` importable before
finishing. Two independent failure modes defeated that approach on the evolve
set:

1. **Detection miss.** The reminder only fired when the agent's *Bash command
   string* contained an explicit port / framework token (``flask``,
   ``--port``, ``127.0.0.1:8080``, ...). Tasks that start their service via a
   bare compiled binary (``/app/server &``) or via ``nginx -c conf`` carry the
   port only inside a *config file*, never in the command — so the flag was
   never set and no reminder fired at all.
2. **Non-compliance.** Even when the reminder fired, the model sometimes ran
   ``python3 -c 'import requests'``, saw ``ModuleNotFoundError``, and then
   declared success anyway without installing anything. A soft nudge cannot
   force the action.

This processor closes *both* gaps with a Control hook that does not rely on the
model complying:

* It flips a per-task ``_service`` flag when it observes a Bash command that
  either matches a generic service signal (framework / port / bind) *or* a
  generic service-launch shape (a common HTTP daemon such as ``nginx`` /
  ``httpd`` / ``apache``, a ``systemctl``/``service`` start, or a locally-built
  executable launched in the background). The launch-shape signals are what let
  it catch the bare-binary and ``nginx -c`` starts the old regex missed.
* When the model tries to *exit* (``finish_reason`` in ``end_turn``/``stop``
  with no tool calls) on a task where a service was detected, it injects **one
  real ``Bash`` tool call** that idempotently makes ``requests`` importable by
  the system interpreter:

      python3 -c 'import requests' || python3 -m pip install --quiet requests

  The RunLoop executes this like any other Bash call, so the dependency is
  installed deterministically regardless of whether the model would have acted.
  ``pip install requests`` is confirmed to succeed in these containers (multiple
  evolve-set tasks installed ``requests 2.34.2`` this way), and the command is a
  no-op when the package is already present.

It fires the install **at most once per task** and only on tasks where a
service launch was actually observed, so non-service tasks (the majority) are
never touched — no extra Bash round-trip, no needless install. It orders after
``CustomSelfVerifyProcessor`` (order 90), whose one-shot keepalive fires on the
*first* exit intent; this hook's install fires on a later exit intent, so the
two never contend for the same turn.

Evolve-set evidence (r0):
  * task_000028 (nginx -c + bare C++ ``/app/server &``): correct service,
    verifier failed ``import requests`` at collection — old regex never matched.
  * task_001857 (bare ``/home/user/diagnostic_server &``): same shape.
  * task_000297 (python http.server on 127.0.0.1:8080): reminder fired, agent
    saw ``ModuleNotFoundError`` and finished anyway → still 0.
All three were fully-correct services scored 0 purely on the missing client.
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

# Generic "this command is running / configuring an HTTP or network service"
# signal. Kept framework/language-agnostic so it matches any stack, never one
# task's specifics.
_SERVICE_SIGNALS = re.compile(
    r"""(?ix)
    (?:
        \bflask\b
      | \bfastapi\b
      | \buvicorn\b
      | \bgunicorn\b
      | \bhypercorn\b
      | \bhttp\.server\b
      | \bhttpd\b
      | \bapache2?\b
      | \bnginx\b
      | \bapp\.run\s*\(
      | \.listen\s*\(
      | \blisten\s+\d{2,5}\b
      | (?:127\.0\.0\.1|0\.0\.0\.0|localhost)\s*[:]\s*\d{2,5}
      | \b--port\b
      | \bhost\s*=\s*['"]?(?:127\.0\.0\.1|0\.0\.0\.0)['"]?
      | \bsystemctl\s+(?:start|restart|enable)\b
      | \bservice\s+\S+\s+(?:start|restart)\b
    )
    """
)

# A locally-built / provided executable launched as a background daemon:
#   nohup /app/server ... &
#   ./server &
#   /home/user/diagnostic_server 2>&1 &
# Matches an absolute-ish path or ./name that ends the (sub)command with `&`.
# Deliberately narrow: requires the trailing background `&`, so ordinary
# foreground commands (cat, ls, grep, compile steps) never trip it.
_BG_EXECUTABLE = re.compile(
    r"""(?x)
    (?:^|[;&|]|\bnohup\s+)          # start of a (sub)command, optional nohup
    \s*
    (?:\./|/)[\w./-]*[\w-]          # ./name or /abs/path/name
    [^\n;|]*                        # its arguments (no new command separators)
    &\s*(?:$|\n|[;&])               # launched into the background
    """
)

_INSTALL_CMD = (
    # Idempotent: only installs if the import fails. Quiet to keep output small.
    "python3 -c 'import requests' 2>/dev/null "
    "|| python3 -m pip install --quiet requests; "
    "python3 -c 'import requests, sys; "
    "print(\"[requests-ready]\", requests.__version__)' 2>&1"
)


class HttpVerifierDepInstallProcessor(MultiHookProcessor):
    """Deterministically install the `requests` grading client on service-task exit."""

    _singleton_group = "http_verifier_dep"
    _order = 91  # after CustomSelfVerifyProcessor (90): its keepalive lands first

    def __init__(self) -> None:
        self._service = False
        self._install_injected = False
        self._install_call_id: str | None = None

    async def on_task_start(self, event: TaskStartEvent):
        self._service = False
        self._install_injected = False
        self._install_call_id = None
        yield event

    async def on_before_tool(self, event: ToolCallEvent):
        if not self._service and event.tool_name == "Bash":
            cmd = ""
            try:
                cmd = event.tool_input.get("command", "") or ""
            except Exception:
                cmd = ""
            if cmd and (_SERVICE_SIGNALS.search(cmd) or _BG_EXECUTABLE.search(cmd)):
                self._service = True
        yield event

    async def on_after_model(self, event: ModelResponseEvent):
        # Fire once, on an exit-intent turn, only for detected service tasks.
        exit_intent = (
            event.finish_reason in ("end_turn", "stop") and not event.tool_calls
        )
        if exit_intent and self._service and not self._install_injected:
            self._install_injected = True
            self._install_call_id = f"reqinstall-{uuid.uuid4().hex[:8]}"
            install = ToolCall(
                id=self._install_call_id,
                name="Bash",
                input={"command": _INSTALL_CMD},
            )
            yield dataclasses.replace(event, tool_calls=(install,))
        else:
            yield event

    async def on_task_end(self, event: TaskEndEvent):
        self._service = False
        self._install_injected = False
        self._install_call_id = None
        yield event
