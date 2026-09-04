# Candidates

Assigned focus: `task_000106_23215092` (data_querying) — reward 0,
`exit_reason=budget_exceeded`, 80 steps, 960s. Diagnosis below.

## Root-cause of the assigned task

The task asks the agent to build a Flask/HTTP API on `127.0.0.1:8000`,
leave it running in the background, and have integration tests query it.

What actually happened (verified from the trajectory body):
1. Agent launches the server in the background: `python3 graph_api.py & ; sleep 2`.
   The server binds port 8000 fine (msg step ~ line 130/614: " * Running on 127.0.0.1:800X").
2. The endpoint returns **HTTP 500** (line 150, 634). This is the *real* bug —
   but the server was launched with `&` and its stderr/traceback was NEVER
   redirected to a file the agent reads, so the 500's root cause stays invisible.
3. To "debug", the agent launches **another** `python3 -c "...app.run..." &`
   which fails with **"Address already in use"** (line 170, 654) — because its
   OWN previous background server still holds the port.
4. Agent misreads the self-inflicted port conflict as the problem, enters a
   kill→restart→recreate-file loop (kill PIDs, switch to 8001, sed the port,
   heredoc-rewrite graph_api.py). Triggers the EditDetection over-edit warning
   16 times. Never once captures the server log to see the actual 500 traceback.
5. Budget exhausted at 80 steps → `budget_exceeded`, no working server.

The `final_pytest` "ModuleNotFoundError: No module named 'requests'" is a
VERIFIER-phase artifact (verifier's own env), not the agent's failure. The
agent's failure is: never got a working server up before budget ran out,
because it chased a self-caused port conflict instead of the real 500.

This is a **harness deficiency**, not a model capability gap: the harness
provides no feedback that (a) surfaces the background server's stderr so the
real error is visible, and (b) tells the agent a fresh "Address already in use"
after it just launched a server is its own prior process — kill/reuse it,
don't restart on a new port.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `ServerLaunchGuard` `MultiHookProcessor` that (a) when a Bash command
backgrounds a long-running server without redirecting output to a log file,
appends a note instructing the agent to capture stdout+stderr to a logfile and
`cat` it after `sleep` so the real startup/500 error is visible; and (b) when a
tool result contains "Address already in use" / "port ... in use" it injects
guidance that the conflict is almost certainly the agent's own prior background
server — free the port with `fuser -k <port>/tcp` (or reuse the running one)
rather than restarting on a new port.

- Tasks affected: task_000106_23215092, task_000297_01ba10b6
  (both server tasks; both died in a port-conflict / restart loop). Generalizes
  to the 9 tasks that launch a background server (000010, 000378, 000958,
  001013, 001035, 001857, 001937).
- Signal: `grep "Address already in use"` matches task_000106 and task_000297;
  `grep "500 INTERNAL"` matches task_000106; `exit_reason=budget_exceeded` on
  106; both launch servers via `... &`. EditDetection over-edit warnings x16 on
  106 confirm the thrash loop.
- Verified (Read):
  - task_000106 line 130/614: " * Running on http://127.0.0.1:800X" then line
    150/634 "Error: HTTP Error 500: INTERNAL SERVER ERROR" — server up but 500,
    no log captured. Line 170/654: "Address already in use / Port 800X is in use
    by another program" immediately after re-launching its own server. Then a
    kill→sed→heredoc→relaunch loop to line 660+.
  - task_000297 body: repeated "The port is still in use. Let me ... kill it."
    with the same TCPServer "Address already in use" traceback recurring across
    multiple steps; agent loops on port-kill instead of progressing.
- Why Control not Instruction: the failure is mechanical and recurs uniformly
  across every background-server task; the corrective signal must fire exactly
  when the port-conflict/undirected-launch pattern appears in tool I/O — a
  static prompt rule fires always (noise on non-server tasks) and, more
  importantly, wouldn't put the *actual server log contents* / the *self-caused
  conflict diagnosis* in front of the agent at the decisive step. An
  `on_before_tool`/`on_after_tool` guard is the same shape already used by
  `BgInstallGuard` and `CustomEditToolProcessor`.
- Why Control not Action: TB2 exposes only `Bash` and that cannot be changed;
  the agent already CAN capture logs and kill ports via Bash — it just doesn't
  know to, at the moment it matters. No new capability is missing.
- Retroactive check (A-corrective): yes — on task_000106, had the guard nudged
  the agent to (1) redirect the server log and read the 500 traceback, and
  (2) recognize the port conflict as its own prior server and `fuser -k` it
  instead of restarting, the agent would have fixed the real bug and left one
  working server bound to 8000 well within budget. On task_000297 the same
  "the port is your own process, free it once and stop restarting" nudge breaks
  the kill/restart loop.
- expected_global_gain: unblocks the background-server failure cluster (2 tasks
  in a hard loop today, 9 tasks total launch servers). Server tasks are the
  highest-elapsed failures (106=960s, 297=386s), so breaking the loop also
  reclaims budget.
- regression_risk: low. Guard only appends advisory text to tool results (never
  blocks a call) and only when the command matches a background-server launch or
  the result text contains a port-conflict string. Non-server tasks never match,
  so no behaviour change there. It appends at most one note per distinct trigger
  (deduped) to avoid spamming context.
- cost_shift: slightly negative-to-neutral expected — a few hundred chars of
  injected guidance per server task, but it should shorten the multi-hundred-step
  thrash loops dramatically (106 used all 80 steps / 960s), net token savings on
  the server cluster.
