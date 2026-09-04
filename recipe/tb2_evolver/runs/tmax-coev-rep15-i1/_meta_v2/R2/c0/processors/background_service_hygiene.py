# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""BackgroundServiceHygieneProcessor.

Closes a structural budget-burn / churn failure mode observed on
Terminal-Bench-2 / tmax tasks that require the agent to stand up (or proxy
to) a long-lived local network service — port-forwarders, mock HTTP APIs,
socket servers.  On these tasks the *tool layer* returns two generic,
content-agnostic failure signatures that the model repeatedly fails to
diagnose:

1. ``command timed out after <N>s`` — the agent ran a *foreground* command
   (an interactive CLI, a curl/probe, its own driver script) that talked to a
   service which was not actually reachable/durable, so the Bash tool blocked
   for its full timeout and returned nothing.  Each such call silently burns
   the timeout's worth of wall-clock budget with zero progress.

2. ``address already in use`` (socket bind failure) — the agent tried to
   (re)start a listener on a port that is still held by a *stale* background
   process it spawned earlier and never reaped, so the new bind fails.  The
   model typically misreads this as "the service is already running, good"
   and moves on, or thrashes restart/kill/restart without ever freeing the
   port.

Both signatures share one harness-actionable root cause: the agent is not
managing the lifecycle of a background service (durable multi-connection
listener, stale-process cleanup, fast bounded reachability probe) and instead
burns budget on hung foreground commands and colliding rebinds.  On
task_000010 this cost three full 120s Bash timeouts (~360s of a 1113s run)
plus seven "address already in use" rebinds, and the run exited
``budget_exceeded`` at the 80-step cap with the manifests never applied.

The agent has exactly one tool (``Bash``) and cannot change its own timeout,
so this is not something a prompt-only rule reliably reaches at the moment it
matters.  This processor supplies the missing lifecycle discipline as a
*mechanical, one-shot* strategy nudge, injected exactly when the churn
signatures accumulate — not on task identity.  It fires at most once per task
and injects a single user message, so it is contract-clean and cheap.

This is a *class* fix, not a task fix: it keys off generic OS/tooling failure
strings (``timed out``, ``address already in use``) that any background-service
task produces, never off task ids or task-specific paths / ports.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    TaskEndEvent,
    TaskStartEvent,
    ToolResultEvent,
)
from harnessx.core.processor import MultiHookProcessor

# A foreground command blocked until the Bash tool's hard timeout and returned
# nothing useful. Generic across every task class.
_TIMEOUT_RE = re.compile(r"timed out after\s+\d+", re.IGNORECASE)

# A socket bind failed because the port is still held (usually by a stale
# background process the agent spawned and never reaped). Matches the common
# phrasings emitted by Python sockets, socat, nc, etc.
_ADDR_IN_USE_RE = re.compile(
    r"address (?:already )?in use|Errno 98|EADDRINUSE", re.IGNORECASE
)

_HYGIENE_MSG = """\
[BackgroundServiceHygiene] Your recent commands show a background-service \
lifecycle problem: foreground commands are hanging until they time out, and/or \
a listener cannot bind because its port is already in use. Repeating the same \
start/probe will keep burning budget. Stop and reset the service lifecycle \
before running anything else:

1. Find and free the port. A "timed out" or "address already in use" almost \
always means a *stale* background process you started earlier still holds the \
port (or a half-open listener is accepting connections but never responding). \
Enumerate what is bound (`ps aux`, `python3 -c "import socket; ..."` probe, \
`fuser`/`lsof`/`netstat` if available) and kill the stale PIDs, then confirm \
the port is actually free before rebinding.
2. Make the listener durable. A single-shot proxy/server that handles one \
connection and exits will make the *next* request hang. Ensure the \
forwarder/server accepts connections in a loop (fork/thread per connection, \
`reuseaddr`) and stays alive in the background.
3. Probe fast, never hang. Before invoking any interactive tool or driver that \
depends on the service, verify reachability with a short, bounded check (a \
socket connect with a 1-2s timeout, or a quick request with an explicit \
timeout). Only proceed once the probe succeeds — do not re-run a command that \
just hung against a service you have not confirmed is answering.

Fix the lifecycle once, verify the endpoint responds, then run your real \
command a single time."""


class BackgroundServiceHygieneProcessor(MultiHookProcessor):
    """One-shot strategy nudge when background-service churn signatures appear.

    Watches tool results for two generic failure signatures (command timeouts
    and port-bind collisions). When their combined count crosses
    ``signal_threshold`` the processor injects a single user message describing
    how to reset a background service's lifecycle (free stale ports, make the
    listener durable, probe with a bounded timeout instead of hanging).

    Fires at most once per task. It is *churn-gated*, not exit-gated: as soon
    as the accumulated churn signatures cross the threshold, the guidance is
    injected as a single user message on the very next model call (whether or
    not the agent is about to call a tool), so it lands while the agent is
    actively thrashing rather than only if it happens to pause. Injecting a
    lone user message before the model call is contract-clean and adds no tool
    round-trip.
    """

    _singleton_group = "bg_service_hygiene"
    # After the self-verify / service-deps exit-intent processors (order 90-91)
    # so their hooks serialize; this one is not exit-gated, it is churn-gated.
    _order = 92

    def __init__(self, signal_threshold: int = 2) -> None:
        # Number of accumulated churn signatures before we intervene. 2 keeps
        # a single transient timeout from tripping it while still firing well
        # before the run drains its budget.
        self._signal_threshold = max(1, int(signal_threshold))
        self._signals = 0
        self._nudged = False
        self._armed = False

    async def on_task_start(self, event: TaskStartEvent):
        self._signals = 0
        self._nudged = False
        self._armed = False
        yield event

    async def on_after_tool(self, event: ToolResultEvent):
        if not self._nudged:
            text = (event.result or "") + " " + (event.error or "")
            if _TIMEOUT_RE.search(text) or _ADDR_IN_USE_RE.search(text):
                self._signals += 1
                if self._signals >= self._signal_threshold:
                    self._armed = True
        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._armed or self._nudged:
            yield event
            return
        # Arm -> fire exactly once, on the next model call after the churn
        # threshold was crossed.
        self._nudged = True
        self._armed = False
        yield dataclasses.replace(
            event,
            messages=event.messages + (Message(role="user", content=_HYGIENE_MSG),),
        )

    async def on_task_end(self, event: TaskEndEvent):
        self._signals = 0
        self._nudged = False
        self._armed = False
        yield event
