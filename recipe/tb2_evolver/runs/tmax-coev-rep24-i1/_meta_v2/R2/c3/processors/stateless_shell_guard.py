"""Stateless-shell background-process guard for Tmax / TB2-style agents.

Root-cause motivation (harness deficiency, not domain knowledge):

The Bash tool in this benchmark runs every command as an *independent*
``docker exec ... bash -lc <command>`` (see
``recipe/tmax_eval/docker_env.py::exec_in``). Each tool call therefore gets a
**fresh, short-lived shell**: a process started in the background with a bare
``&`` in one call is a child of that call's bash, and it tears down / orphans
when the call returns. It is NOT co-scheduled with a command issued in a
*later* tool call.

Agents on daemon / service / resource-monitor tasks routinely miss this. They
launch a long-lived background process in one Bash call, then launch the
workload it is supposed to observe in a *separate* Bash call, and are baffled
when the two never coexist (the daemon "sees no work and exits", or the
workload runs unmonitored). This drives repeated failed tests and, on weak
models, degenerate repetition / length loops that burn the whole step budget
(observed: the monitor task looped to ``budget_exceeded`` on exactly this
mistake).

This is a structural fact about the execution model that is invisible from the
task text and from any single tool result, and it recurs across a whole class
of tasks. Supplying that missing context *at the moment the antipattern
appears* is a generic harness mechanism — not task-specific knowledge. The
hint is phrased as general strategy ("run co-running processes in a single
command, or use nohup") and contains no task IDs, paths, or constants lifted
from any trajectory.
"""

from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import (
    BeforeModelEvent,
    Message,
    ModelResponseEvent,
    TaskEndEvent,
    TaskStartEvent,
)
from harnessx.core.processor import MultiHookProcessor

# A command segment that ends with a bare ``&`` (backgrounding), captured up to
# the ``&`` so we can inspect what is being backgrounded. Excludes ``&&`` (which
# is a logical-and, not backgrounding) via the negative lookahead / lookbehind.
_BG_SEGMENT = re.compile(r"([^&\n;]+?)\s&(?!&)")

# Indicators that the backgrounded thing is a *persistent* process worth
# co-scheduling (a script / interpreter / loop / sleep), rather than a trivial
# one-shot. Kept general — no task-specific names.
_PERSISTENT_HINT = re.compile(
    r"(python\d?(\.\d+)?\b|\bbash\b|\bsh\b|\.py\b|\.sh\b|\bwhile\b|"
    r"\bfor\b|\bsleep\b|\bnohup\b|\bsetsid\b|\btail\s+-f\b|\bserver\b|"
    r"\bdaemon\b|\brun\b)",
    re.IGNORECASE,
)

# Constructs that mean the agent has ALREADY handled cross-call persistence or
# co-scheduling correctly — do not nag in these cases.
_ALREADY_HANDLED = re.compile(
    r"(\bnohup\b|\bsetsid\b|\bdisown\b|\bwait\b|&&|\bsystemd\b|\bsystemctl\b)",
    re.IGNORECASE,
)

_HINT = (
    "Execution-model note: each Bash tool call runs in its OWN fresh, "
    "short-lived shell. A process you start in the background with a bare "
    "`&` in one tool call does NOT keep running alongside commands you issue "
    "in a LATER, separate tool call — that shell (and its background child) "
    "goes away when the call returns. That is almost certainly why a "
    "background process you started earlier appears to have vanished or "
    "'exited immediately'.\n"
    "To observe or test two processes running at the same time (e.g. a "
    "monitor/daemon plus the workload it watches), launch them TOGETHER "
    "inside a SINGLE Bash command, for example:\n"
    "    ( long_running_process ) & sleep 0.3; ( the_workload ); wait\n"
    "so both share one shell for the whole test. If you truly need a process "
    "to outlive the call, start it with `nohup ... >logfile 2>&1 &` and then "
    "inspect the logfile in a later call. Do not keep retrying the "
    "background-then-separate-call pattern."
)


class StatelessShellBackgroundGuard(MultiHookProcessor):
    """Inject execution-model context when the agent backgrounds a process in
    one Bash call and then issues a separate call, so the two never coexist."""

    _singleton_group = "tmax_stateless_shell_guard"
    # Order after length-recovery (5) and correction layers; this is advisory
    # context, not a structural rewrite.
    _order = 8

    def __init__(self, max_fires: int = 3) -> None:
        self.max_fires = max(1, int(max_fires))
        self._pending_hint: str = ""
        self._armed: bool = False  # a prior call backgrounded a persistent proc
        self._fires: int = 0

    async def on_task_start(self, event: TaskStartEvent):
        self._pending_hint = ""
        self._armed = False
        self._fires = 0
        yield event

    @staticmethod
    def _bash_commands(event: ModelResponseEvent) -> list[str]:
        cmds: list[str] = []
        for tc in event.tool_calls or ():
            if getattr(tc, "name", "") != "Bash":
                continue
            inp = getattr(tc, "input", None) or {}
            cmd = inp.get("command") if isinstance(inp, dict) else None
            if isinstance(cmd, str) and cmd.strip():
                cmds.append(cmd)
        return cmds

    @classmethod
    def _backgrounds_persistent(cls, cmd: str) -> bool:
        """True if the command backgrounds a persistent process with a bare &
        and has NOT already handled co-scheduling / persistence itself."""
        if _ALREADY_HANDLED.search(cmd):
            return False
        for seg in _BG_SEGMENT.findall(cmd):
            if _PERSISTENT_HINT.search(seg):
                return True
        return False

    @classmethod
    def _coschedules(cls, cmd: str) -> bool:
        """True only if a SINGLE command genuinely co-schedules a background
        process with other foreground work in the same shell — i.e. the agent
        is testing concurrency correctly in-call. Detected by an explicit
        ``wait``, or a real bare ``&`` (backgrounding) followed by additional
        foreground commands. ``&&`` (logical-and) is NOT co-scheduling and must
        not trigger this."""
        if re.search(r"\bwait\b", cmd):
            return True
        # Neutralise ``&&`` so it cannot masquerade as a backgrounding ``&``.
        neutralised = cmd.replace("&&", "\x00\x00")
        # A bare ``&`` that is followed (later in the string) by more
        # non-whitespace, non-terminator work means "background this, then keep
        # going in the same shell" — genuine co-scheduling.
        if re.search(r"&\s*[^\s;)&][^&]*\S", neutralised):
            return True
        return False

    async def on_after_model(self, event: ModelResponseEvent):
        cmds = self._bash_commands(event)
        if not cmds:
            # Non-Bash / no-tool turn: leave state as-is (the armed flag persists
            # so we still catch the next separate Bash call).
            yield event
            return

        if self._fires >= self.max_fires:
            yield event
            return

        # If any command in this turn already co-schedules correctly, the agent
        # has learned the lesson — disarm and stay quiet.
        if any(self._coschedules(c) for c in cmds):
            self._armed = False
            yield event
            return

        # If we were armed by a previous call and this new (separate) call still
        # does not co-schedule, the antipattern is confirmed — arm the hint.
        if self._armed:
            self._pending_hint = _HINT
            self._fires += 1
            self._armed = False

        # Arm for next turn if this call backgrounds a persistent process alone.
        if any(self._backgrounds_persistent(c) for c in cmds):
            self._armed = True

        yield event

    async def on_before_model(self, event: BeforeModelEvent):
        if not self._pending_hint:
            yield event
            return
        hint = self._pending_hint
        self._pending_hint = ""
        msgs = list(event.messages)
        # Respect the before_model contract: if the last message is already a
        # user turn, fold the hint into it; otherwise append one user message.
        if msgs and getattr(msgs[-1], "role", None) == "user":
            last = msgs[-1]
            base = last.content if isinstance(last.content, str) else ""
            merged = (base + "\n\n" + hint) if base else hint
            msgs[-1] = Message(role="user", content=merged)
            yield dataclasses.replace(event, messages=tuple(msgs))
        else:
            yield dataclasses.replace(
                event,
                messages=tuple(msgs) + (Message(role="user", content=hint),),
            )

    async def on_task_end(self, event: TaskEndEvent):
        self._pending_hint = ""
        self._armed = False
        self._fires = 0
        yield event
