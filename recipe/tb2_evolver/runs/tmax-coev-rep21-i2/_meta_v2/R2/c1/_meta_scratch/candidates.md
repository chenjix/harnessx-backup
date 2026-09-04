# Candidates — R2 / c1

Assigned focus: `task_000011_d089ef35` (fails, reward 0).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Rewrite bare-`&` background **service** launches in Bash commands to run
under `setsid ... </dev/null >>log 2>&1 &` so the process detaches from the
controlling terminal / agent session and survives to the verifier phase.

- Tasks affected: task_000011_d089ef35 (primary); generalises to the TB2
  "write a server, run it in the background" task class (the playbook names
  this structural failure explicitly).
- Signal: `result.json` — `exit_reason=done`, `finished=no_tool_calls`,
  agent's own in-session probe of the server succeeds, but `final_pytest`
  fails with `ConnectionRefusedError: [Errno 111]` / "Is the server running?".
  The playbook "What trajectories will NOT tell you" lists exactly this:
  *"Background process dies after agent exits."*
- Verified (Read of task_000011_d089ef35.messages.json):
  - msg 26/28/36/38: server always launched as
    `/home/user/mesh_server &` — a bare background job, **no** `setsid` /
    `nohup` / `disown`.
  - msg 45/46: agent confirms port 9090 is LISTEN (`0x2382`) during the
    session; msg 53 `nc` probe returns correct MSE for all 4 quadrants
    (8.25 / 17.25 / 68.25 / 93.25) — the solution logic is fully correct.
  - msg 53 also shows the process history: PIDs 66/123/491/648 are
    `[mesh_server] <defunct>` zombies, one live PID 705 — the agent
    repeatedly killed/relaunched, and the surviving child is a job of an
    ephemeral tool shell.
  - result.json `final_pytest.output_tail`: 5 tests fail, all
    `Connection refused` to 127.0.0.1:9090 — the server is gone by the time
    the external verifier connects.
- Why Control not Instruction: the agent already "knows" it must run a
  persistent server and believes it did (it verified the port). A prompt rule
  ("use nohup/setsid") is model knowledge the small model repeatedly fails to
  apply under thrash (it killed and relaunched with bare `&` five times). A
  mechanical `on_before_tool` rewrite guarantees detachment regardless of the
  model's shell discipline — the same guard that fires uniformly across every
  service task, which an instruction cannot promise.
- Why Control not Action: the only tool is Bash and cannot be changed
  (playbook hard limit); the fix is transforming the command the agent already
  issues, not adding an action.
- Retroactive check (A-corrective): yes — had `/home/user/mesh_server &` been
  rewritten to `setsid /home/user/mesh_server </dev/null >>… 2>&1 &`, the
  server would have detached into its own session and survived session
  teardown; the verifier connecting to 127.0.0.1:9090 would have reached the
  (correct) server and all 5 tests would have passed.
- expected_global_gain: flips task_000011 and any other task in the
  server-in-background class whose only failure is post-session process death
  (the verifier ConnectionRefused cluster). Sole clean member this round is
  task_000011 — task_000313 shares the ConnectionRefused symptom but its
  root cause is a kill/relaunch loop terminated by the loop detector, a
  different mechanism; this candidate does not claim it.
- regression_risk: Low. Rewrite is heavily gated: Bash-only; standalone `&`
  only (`&&`, `2>&1`, `>&` excluded by look-around); launcher-shaped token
  only (exec path or known runtime); skips anything already using
  setsid/nohup/disown; skips install/build (owned by BgInstallGuard); on any
  parse doubt returns the command unchanged; never blocks, never raises.
  `setsid <cmd> &` is standard-compatible and the added stdio redirection only
  applies when the author supplied none. Worst case for a mis-fire: a
  short-lived foreground-intended job is detached and its output goes to a log
  instead of the tool result — but the launcher gate + standalone-`&` gate make
  this unlikely, and detaching a process the agent chose to background is
  benign.
- cost_shift: Negligible. No model tokens added (silent command rewrite); one
  extra `setsid` exec per backgrounded service launch. Removes the wasted
  verifier-phase failure on affected tasks.

Rollback trigger: if R3 shows a previously-passing service task regress (e.g.
a backgrounded process the agent intended to be transient now lingers and
interferes) or task_000011 still fails with ConnectionRefused (meaning the
killer is cgroup/container teardown, not session SIGHUP, and setsid is
insufficient), revert this processor.
