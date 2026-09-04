# Candidates — R3

Baseline R2 = 11/50 (0.22). R1 removed context-overflow crashes; R2 broke
identical-command loops (several loopers now exit `done`/short instead of
`budget_exceeded`, but still fail on correctness). No `exit_reason=error`
remain. Dominant remaining failure shapes:

- A cluster of HTTP/socket-service tasks where the agent builds a server,
  starts it with `nohup ... &`, and the process is SIGTERM'd (`exit 143`,
  `[server] <defunct>` zombies) — so at verifier time nothing is listening
  (`Connection refused`). Root cause is a *harness* behaviour, not a model
  gap: `HarborSandbox.exec` runs every command as `setsid bash -c '<cmd>' &`
  and `kill_running()` sends `kill -15 -"$PGID"` to that command's process
  group. A server started with plain `nohup ... &` inherits the exec'd bash's
  PGID, so it is killed with the command. The agent has the capability to fix
  this (`setsid`) but lacks the knowledge that it must detach a service into
  its OWN session to survive across Bash calls.
- Two service tasks die at the *verifier* with `ModuleNotFoundError: requests`
  because the existing `VerifierDepGuardProcessor` only fires on clean
  exit-intent, and these tasks hit `budget_exceeded` (never reach exit).

## Candidate C-003
[lens: failure | lever: instruction | intent: corrective]

Add a general strategy rule to `system_prompt.txt`: to keep a long-running
service (HTTP server, daemon, listener) alive across separate Bash calls and
into the post-agent verification phase, launch it detached in its OWN new
session (e.g. `setsid <cmd> >log 2>&1 < /dev/null &`), and re-confirm it is
still listening immediately before finishing. Strategy-only; no task literals.

- Tasks affected: task_000796_828a72cf, task_000097_d9d1d187,
  task_000910_16cc0daf, task_002146_0bc2994c, task_001201_1340f4e2,
  task_000348_31fb8c8a (server-alive cluster; 6 tasks show background process
  + SIGTERM/defunct signals)
- Signal: tool results carry `(exit 143)` on `nohup ... &` launch commands;
  `ps` shows `[server] <defunct>` zombies; verifier `output_tail` reports
  `Connection refused. Is the service listening on 127.0.0.1:PORT?`.
- Verified (Read messages.json):
  - task_000796_828a72cf steps 52-78: agent repeatedly `pkill`/rebuild/
    `nohup ./server >/tmp/server.log 2>&1 &; sleep 2`; every launch turn's
    tool result ends `(exit 143)` and `ps aux` lists `[server] <defunct>`
    zombies. Agent narrates "The server was killed again." The final launch
    (step 77) also returns `(exit 143)`, so nothing is listening at verify
    time. Verifier: `Connection refused` on 127.0.0.1:8443.
  - task_000097_d9d1d187: budget_exceeded; verifier `Connection refused. Is
    the service listening on 127.0.0.1:9090?` — server not alive at verify.
  - task_000348_31fb8c8a step-45 body: server *was* reachable (got JSON back)
    but only because the agent's last call happened to leave it up; wrong
    algorithm caused the correctness fail — this task confirms the mechanism
    (a surviving server yields a real HTTP response) while being a partial
    capability gap.
- Why Instruction not Control: the agent already *can* detach a process
  (`setsid`/`disown` are in the image); it simply doesn't know this sandbox
  kills the command's process group between calls. A Control `on_before_tool`
  hook that rewrites backgrounding commands would have to parse arbitrary
  shell (nohup/&/subshells/pipelines), is brittle, and would risk mangling
  legitimate foreground commands — high regression surface for a knowledge
  gap. The knowledge is transferable across every service task, so it belongs
  in the prompt.
- Retroactive check (A-corrective): yes for the "server-dies" subset — if the
  agent had launched with its own `setsid` session, the server would have
  survived `kill_running`, stayed listening, and the verifier's HTTP probe
  would have connected (task_000796, task_000097). For task_000348 the fix is
  necessary-not-sufficient (server survives but algorithm still wrong) — that
  one is counted as evidence for the mechanism, not a predicted flip.
- expected_global_gain: closes a structural "service killed between Bash
  calls" failure across the socket/HTTP-service cluster (6 tasks span 5
  domains). Flips the tasks whose only blocker was a dead listener; gives the
  rest a fair correctness attempt.
- regression_risk: near-zero. The rule is additive strategy text; it does not
  change tool behaviour. Passing tasks that never start a service ignore it.
  The only behavioural nudge is "detach services and re-verify listening",
  which cannot hurt a task that doesn't run a service.
- cost_shift: flat-to-slightly-down. A surviving server removes the repeated
  kill/rebuild/restart loops (task_000796 burned ~13 steps fighting the kill);
  the added prompt text is a few hundred tokens once per task.

## Candidate C-004 (DEFERRED — not shipped this round)
[lens: failure | lever: configuration | intent: corrective]

Deferred: only 2 tasks, both also `budget_exceeded` (uncertain flips even
with the fix), and the cleanest implementation (inject a Bash ensure at
`on_task_start`) is intrusive to the agent's first turn and adds replay-gate
risk. Not worth the regression surface this round; revisit if the
server-alive fix lands and the requests-collection cluster persists.

Make `VerifierDepGuardProcessor` also ensure its dep allow-list at
`on_task_start` (proactive), not only on exit-intent, so tasks that end via
`budget_exceeded` (which never reach exit-intent) still get `requests`/`pyyaml`
importable for the verifier.

- Tasks affected: task_000910_16cc0daf, task_002146_0bc2994c
- Signal: verifier `output_tail` = `ModuleNotFoundError: No module named
  'requests'` at pytest collection; both `exit_reason=budget_exceeded`.
- Verified (Read result.json): task_000910_16cc0daf final_pytest ends
  `import requests / ModuleNotFoundError / Interrupted: 1 error during
  collection`; task_002146_0bc2994c identical shape. Both never reached
  exit-intent (80-step budget_exceeded), so the existing exit-only guard
  never fired.
- Why Configuration not Control-rewrite: the mechanism (ensure common
  verifier libs importable) and the processor already exist; the only defect
  is *when* it fires. Adding a proactive `on_task_start` ensure via the same
  guarded, idempotent Bash exec closes the budget_exceeded gap without a new
  component. (Implemented as a small subclass so the exit-intent behaviour is
  preserved AND a task-start ensure is added.)
- Retroactive check (A-corrective): partial — if `requests` had been present
  the verifier would at least *collect* and run; whether these two flip
  depends on solution correctness (both were budget_exceeded, so uncertain).
  The guaranteed win is converting a collection-error 0 into a fair scored
  run. Kept because cost/risk are trivial and it protects any future service
  task that times out.
- expected_global_gain: removes the "verifier can't even import requests"
  guaranteed-0 for any task that ends without clean exit-intent.
- regression_risk: near-zero — idempotent no-op when the module is present;
  the proactive check is a direct sandbox-style exec injected once, guarded
  with `|| true`.
- cost_shift: +1 tiny Bash round-trip per task at start (fast import check);
  no-op install cost only on images actually missing the lib.
