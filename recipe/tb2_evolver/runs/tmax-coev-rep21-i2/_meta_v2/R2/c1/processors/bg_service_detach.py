# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
"""BgServiceDetachProcessor — make backgrounded long-lived services survive
session teardown so the TB2 verifier phase can reach them.

Structural gap (see tb2-playbook "Background process dies after agent exits"):
the agent's only tool is ``Bash``, and each Bash tool call executes in an
ephemeral shell. When the agent launches a persistent service with a bare
``&`` background operator (e.g. ``./server &`` or ``python3 app.py &``) the
child is a job of that ephemeral shell. When the shell exits at the end of the
tool call — and again when the whole agent session tears down before the
verifier runs — the backgrounded child can receive SIGHUP / be reaped as part
of process-group/session cleanup. The service *appears* alive during the agent
session (subsequent Bash calls run milliseconds later) but is gone by the time
the external verifier connects, producing ``ConnectionRefusedError`` even
though the agent's own in-session tests passed.

Fix (Control, on_before_tool): when a Bash command backgrounds a launcher-shaped
process with a *standalone* ``&`` and is not already detached (no ``setsid`` /
``nohup`` / ``disown``), rewrite that backgrounded segment to run under
``setsid`` (new session, detached from the controlling terminal so SIGHUP on
shell exit cannot reach it) with stdio redirected to a log file (so a closed
client pipe cannot SIGPIPE it). The transform is deliberately conservative:

  * only fires on the ``Bash`` tool;
  * only touches a *standalone* ``&`` (``&&``, ``2>&1``, ``>&`` are excluded);
  * only when the backgrounded token is a recognised launcher shape
    (an executable path like ``./x`` / ``/abs/x`` or a known runtime such as
    python/node/java/gunicorn/uvicorn/flask/php/ruby/…);
  * skips commands already using ``setsid`` / ``nohup`` / ``disown``;
  * skips install/build commands (handled/blocked upstream by BgInstallGuard);
  * never blocks the call and never raises — on any doubt it passes the
    command through unchanged.

This is a mechanism fix that generalises to every "write a server, run it in
the background" task class, not a single task: it injects no task-specific
constants, paths, or identifiers.
"""
from __future__ import annotations

import dataclasses
import re

from harnessx.core.events import ToolCallEvent
from harnessx.core.processor import MultiHookProcessor

# Already-detached markers — if any is present we leave the command alone.
_ALREADY_DETACHED_RE = re.compile(r"\b(?:setsid|nohup|disown)\b")

# Install/build launchers are owned by BgInstallGuard (which blocks them when
# backgrounded). Do not double-handle them.
_INSTALL_RE = re.compile(
    r"\b(?:apt(?:-get)?|pip3?|npm|yarn|pnpm|make|cargo|mvn|gradle|cmake)\b"
)

# A standalone background operator: a single '&' that is not part of '&&' and
# not part of a redirection ('>&', '2>&1', '&>'). We match the launcher token
# that precedes it. Group 1 = the backgrounded command segment (from the start
# of a line or after a shell separator up to the '&').
#
#   <sep> <launcher> <args...> &
#
# Launcher shapes accepted:
#   * ./name  ../name  /abs/path/name        (explicit executable path)
#   * a bareword that is a known long-lived runtime / server
_LAUNCHER = (
    r"(?:"
    r"\.{0,2}/[^\s;&|]+"                       # ./x  ../x  /abs/x
    r"|(?:python3?|python[\d.]*|node|deno|bun|java|ruby|perl|php|go"
    r"|gunicorn|uvicorn|hypercorn|flask|uwsgi|waitress|daphne"
    r"|http-server|serve|rails|puma|unicorn|nginx|httpd|redis-server"
    r"|mysqld|postgres|mongod|memcached|socat|ncat)\b"
    r")"
)

# The backgrounded segment starts at a line boundary or after a shell command
# separator (; & | newline). We capture from there to the standalone '&'.
_BG_SERVICE_RE = re.compile(
    r"(?P<seg>(?:(?<=^)|(?<=[;\n&|]))\s*"    # start of segment
    rf"{_LAUNCHER}"                          # a launcher token …
    r"[^\n;&|]*?)"                            # … and its args (no separators)
    r"(?P<amp>(?<![&>0-9])&(?!&))"           # standalone background operator
    r"(?P<rest>[^&]|$)",                      # lookahead: not '&&'
)

_LOG = "/tmp/harnessx_bg_service.log"


def _detach_segment(m: re.Match) -> str:
    seg = m.group("seg")
    rest = m.group("rest")
    # Split leading whitespace/separator prefix from the actual command so the
    # rewrite preserves the surrounding shell structure.
    stripped = seg.lstrip()
    prefix = seg[: len(seg) - len(stripped)]
    if not stripped:
        return m.group(0)
    # If the segment already redirects stdout, keep the author's redirection;
    # otherwise send stdio to a log so a closed client pipe can't SIGPIPE it.
    if re.search(r"(?<![0-9])>|>>", stripped):
        redir = ""
    else:
        redir = f" >>{_LOG} 2>&1 </dev/null"
    # setsid puts the process in a fresh session detached from the controlling
    # terminal; the trailing '&' backgrounds it within the current shell too.
    return f"{prefix}setsid {stripped}{redir} &{rest}"


class BgServiceDetachProcessor(MultiHookProcessor):
    """Rewrite bare-``&`` background service launches to survive session teardown."""

    _singleton_group = "bg_service_detach"
    # Run AFTER BgInstallGuard (_order=15) so blocked install/build backgrounding
    # never reaches us, but before the tool executes.
    _order = 16

    async def on_before_tool(self, event: ToolCallEvent):
        if event.tool_name != "Bash":
            yield event
            return

        command = event.tool_input.get("command", "")
        if not command or "&" not in command:
            yield event
            return
        if _ALREADY_DETACHED_RE.search(command):
            yield event
            return
        if _INSTALL_RE.search(command):
            yield event
            return
        if not _BG_SERVICE_RE.search(command):
            yield event
            return

        try:
            new_command = _BG_SERVICE_RE.sub(_detach_segment, command)
        except Exception:
            yield event
            return

        if new_command == command:
            yield event
            return

        new_input = dict(event.tool_input)
        new_input["command"] = new_command
        yield dataclasses.replace(event, tool_input=new_input)
