# Candidates — R2 / c4

Assigned focus: `task_000118_3043e92d` fails (`exit_reason=done`,
`finished=no_tool_calls`, steps=12, reward=0).

## Diagnosis of the assigned task

The agent wrote `/home/user/deployment_monitor.py`, ran it in the background,
ran `run_deployment.sh`, then declared success. The grader failed:
`Peak log directory size was 209715200 bytes` (200 MB) vs a 45 MB threshold —
the monitor never throttled anything. Root cause of the *code*: the agent's
`find_running_workers()` does `subprocess.Popen([WORKER_SCRIPT, "1"], ...)` —
it *launches a new worker* and iterates over the Popen object instead of
listing the real worker PIDs, so SIGSTOP/SIGCONT were no-ops. That specific
bug is a **model capability gap** — the harness cannot fix bad Python logic.

The **harness-addressable** part: the agent declared "done" after verifying
only the *quiescent final state* of a dynamic system. Its checks were
`ls -la /home/user/logs/` (all 0 bytes, because workers had already exited and
the final truncate cleared them) and `du -sh /home/user/logs/` (4 KB). Neither
measures the *peak/transient* size the grader checks. The existing
`CustomSelfVerifyProcessor` DID fire (`_tb2_self_verify` tool call in the
trajectory), but the model rubber-stamped it: its post-checklist turn was
`ls -lh /home/user/deployment_monitor.py` (existence of the script itself),
then it exited. The one-shot prose checklist did not force a substantive
re-exercise of the acceptance behaviour.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a second-stage exit gate (`SubstantiveVerifyGuard`) that fires exactly ONE
extra, sharper verification prompt when the base self-verify checklist was
answered with only a trivial existence check (no re-execution / re-inspection
of the solution).

- Tasks affected: task_000118_3043e92d, task_000667_2d762a00
  (both `exit_reason=done`, `finished=no_tool_calls`, reward=0; both had the
  base `_tb2_self_verify` checklist fire and answered it with a hollow check).
- Signal: `agent.exit_reason=done` + `finished=no_tool_calls` on a reward=0
  task where the trajectory shows a `_tb2_self_verify` tool call followed by a
  single trivial `ls`/`cat`/`du` Bash command and an immediate exit — i.e. the
  first-stage checklist was rubber-stamped.
- Verified (Read):
  - task_000118 messages.json: after `_tb2_self_verify` (id `sv-f092565a`),
    the agent ran `ls -lh /home/user/deployment_monitor.py` and then emitted a
    final no-tool-call summary "SUCCESS-style" message. Its only prior
    "verification" was `ls -la /home/user/logs/` (0-byte files) and
    `du -sh /home/user/logs/` (4.0K) — final quiescent state, never the peak.
    final_pytest: `Peak log directory size was 209715200 bytes ... exceeds ...
    45000000`.
  - task_000667 messages.json: after `_tb2_self_verify`, the agent ran
    `ls -lh /home/user/mre_output.txt` (an unrelated output) and exited; it
    never re-read `setup.py`. final_pytest: `The bug in setup.py was not fixed.
    It still references 'fast_math.cpp'.` A `grep fast_math.cpp setup.py`
    (classified substantive by the guard) would have surfaced the miss.
- Why Control not Instruction: the base checklist is *already* an Instruction
  (the `_SELF_VERIFY_MSG` prose in `CustomSelfVerifyProcessor`) and the weak
  model demonstrably skims it — adding more prose to that one-shot message
  changes nothing because the model exits after a token `ls`. The fix is a
  *mechanical* second gate that (a) inspects whether the agent actually ran a
  substantive command since the checklist and (b) refuses the exit exactly
  once if not. Detecting "was any substantive verification command run since
  the checklist fired" and re-injecting on that condition is a stateful hook
  around the loop, not a prompt rule the model can ignore.
- Why not tighten `CustomSelfVerifyProcessor` in place: it is a shared TB2
  harness class (read-only `benchmarks/`); it also fires unconditionally on
  the first exit. A separate processor keyed on *hollow* verification keeps the
  first-stage behaviour intact and adds a second stage only for the failure
  shape, minimising regression surface.
- Retroactive check (A-corrective): yes (plausible). Both tasks' underlying
  errors were *observable* by re-exercising the real scenario — task_000118 by
  re-running the deployment and measuring peak size while it ran (would show
  200 MB), task_000667 by grepping setup.py for the token the requirement
  forbids. A gate that forces one such substantive re-check before exit gives
  the model the observation it needs to notice its solution is wrong and
  attempt a fix. It is not a guarantee (the model may still fail to fix the
  bug), but it removes the false-success short-circuit that guaranteed failure.
- expected_global_gain: attacks the "premature-done / hollow-verification"
  cluster (5 tasks in this partial set are `done/no_tool_calls/reward=0`;
  ≥2 — 118, 667 — share the exact rubber-stamped-checklist mechanism). The
  guard generalises: any task whose acceptance criterion is about behaviour
  during a run, or a specific file property, benefits from being forced to
  measure it rather than peek at final state.
- regression_risk: LOW. The guard fires at most once, only after the base
  checklist already fired, and only when the agent ran *zero* substantive
  commands since — so tasks that verify properly (incl. passing task_000587,
  task_000748, which both re-run their solutions) never see it. Worst case for
  a task the model genuinely finished: one extra turn + one extra Bash command,
  then it exits regardless (the guard never fires twice, never terminates the
  run). Budget/loop clusters never reach exit-intent, so they are untouched.
- cost_shift: near-neutral to slightly positive. Adds at most one extra
  round-trip + one Bash call on the subset of tasks that reach exit with hollow
  verification. No effect on the dominant budget/loop clusters. Negligible vs
  the 80-step stalls those other guards already address.
