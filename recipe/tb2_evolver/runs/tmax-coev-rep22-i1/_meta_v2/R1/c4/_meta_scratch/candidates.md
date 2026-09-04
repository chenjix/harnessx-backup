# Candidates

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Extend the one-shot self-verify exit checklist with a process-lifecycle
final-state item: reconcile the *final* runtime process state (alive /
torn-down / correct PID-file identity / within resource bound) against the
lifecycle the task actually asks for.

- Tasks affected (same mechanism — agent declares done without reconciling
  final process state with the graded lifecycle):
  - task_000140_01c78b42 — teardown not verified; verifier finds lingering
    `vm_service` processes.
  - task_001090_c61c71f2 — PID file points at a `bash` wrapper; verifier
    requires `/proc/<pid>/comm == monitor` (the compiled binary).
  - (adjacent, same theme) task_000118_3043e92d — unbounded background
    workers; peak log size exceeds the stated limit.
- Signal: `agent.finished=no_tool_calls`, `exit_reason=done`,
  `initial_pytest.passed=true` but `final_pytest` fails on a
  process/lifecycle assertion (`test_no_lingering_service_processes`,
  `test_pid_file_and_process`). The stock `CustomSelfVerifyProcessor`
  checklist item 5 only says "confirm running services are still alive" —
  it never covers teardown, PID-file identity, or resource bounds, and
  biases the agent toward keeping processes alive.
- Verified (Read):
  - task_000140 final assistant message (step ~12) declares "gracefully
    stops the service by sending SIGTERM to the PID" and exits; verifier
    tail: `AssertionError: Lingering vm_service processes found:
    ['339', '599', '813']` — the agent never ran `pgrep -f vm_service`
    after teardown to confirm nothing lingered.
  - task_001090 verifier tail: `Process name is 'bash', expected
    'monitor'` — agent wrote a PID file for a shell wrapper and never
    checked `/proc/<pid>/comm` against the required binary identity.
- Why Instruction not Control: the capability is already present — the
  agent has `Bash` and can run `pgrep`, `os.kill(pid,0)`,
  `cat /proc/<pid>/comm`, and `du`. It simply is not prompted to reconcile
  final state with the required lifecycle, and the existing nudge biases
  the wrong way for teardown tasks. A Control hook cannot mechanically
  determine per-task whether the correct end-state is "alive" vs
  "cleaned up" vs "bounded" — that judgement must stay agent-side driven
  by the task text. This is a targeted edit to the harness's own
  verification nudge (a `MultiHookProcessor` subclass that only changes
  the injected checklist text), so mechanically it is a one-shot
  instruction injection, not a new control behaviour. No task-specific
  identifiers, ports, or paths appear in the addendum.
- Why not Action: TB2 exposes only `Bash`; no new tool is addable or
  needed.
- Retroactive check (A-corrective): yes — had the checklist prompted
  "for a stop/cleanup task confirm `pgrep -f <name>` returns nothing;
  for a keep-alive task confirm the PID-file process identity", the agent
  in task_000140 would have re-run pgrep, seen the lingering process, and
  killed all matches; in task_001090 it would have seen `comm==bash` and
  launched the compiled binary directly. The failing assertions are
  exactly the checks the addendum names.
- expected_global_gain: flips the service-lifecycle final-state cluster
  (>=2 system_administration tasks graded on post-exit process state);
  generalizes to any future task whose verifier inspects lingering
  processes, PID-file identity, or background resource bounds.
- regression_risk: low. The processor fires at most once, only on the
  first no-tool-call exit attempt (identical trigger to the stock
  processor it replaces), and only appends text — no change to
  non-service tasks' control flow. Worst case: a few extra verification
  Bash calls on service tasks.
- cost_shift: negligible; +1 short injected message and possibly a
  handful of extra verification Bash calls on service-lifecycle tasks
  only. No effect on the ~40 non-service tasks.
