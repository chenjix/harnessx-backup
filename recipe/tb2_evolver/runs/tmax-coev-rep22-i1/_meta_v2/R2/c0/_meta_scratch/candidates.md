# Candidates — R2 c0

Assigned focus: `task_000010_644ab1c2` (budget_exceeded, 80 steps, reward 0).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `KilledOutputDiagnosticProcessor` that detects the opaque
`(exit <137|143|124>, no output captured)` sandbox result, surfaces that the
command was SIGKILLed/timed-out (not "produced no output"), and injects a
one-shot escalating strategy for running persistent services detached with
output capture — breaking the identical-retry loop.

- Tasks affected: task_000010_644ab1c2 (assigned). Same mechanism recurs on the
  broader "long-running service / daemon / port-forward" class the R1 journal
  already flagged as high-step non-converging loops (task_001321_658ce4a8,
  task_000958_4bb2b05d, task_001031_a8f0eb37) — any task where a blocking
  service hits the per-command 30s wall returns this identical opaque line.
- Signal: `exit_reason=budget_exceeded`, `steps=80`. In `messages.json` the tool
  result `(exit 137, no output captured)` recurs identically ~13× (msgs
  5,7?,10,14,18,...,57) each following the *verbatim* command block
  `python3 /home/user/port_forward.py &; sleep 2; <port check>`.
- Verified (Read messages.json):
  - msg 4 command = `pkill ...; python3 /home/user/port_forward.py &; sleep 2; ss ... grep 9090`; msg 5 result = `(exit 137, no output captured)`.
  - msg 43 command byte-identical to msg 4; msg 44 result identical
    `(exit 137, no output captured)`. msg 56 identical again.
  - Root cause confirmed in `benchmarks/terminal_bench_2/harbor_sandbox.py:84-114`:
    every command is wrapped `setsid bash -c <cmd> & ...; wait $_hx_pid` with a
    30s timeout; on kill it emits `(exit {rc}, no output captured)`. The agent's
    launch-and-test-in-one-command pattern blocks on `wait`, gets SIGKILLed at
    30s, and the model reads the empty line as "port not listening yet" and
    reruns verbatim.
- Why Control not Instruction: the missing information is *runtime* — which
  specific results were kills vs empty output — and cannot be pre-stated in the
  system prompt for every task. A static prompt rule ("beware exit 137") would
  fire on every task regardless of whether a kill occurred, adding noise on the
  ~28-step passing cluster; the Control hook fires only when the killed shape is
  actually observed. Not Action: only `Bash` exists in TB2 (playbook) and the
  agent already can launch detached processes — the gap is that the sandbox
  strips the kill signal, which post-processing recovers.
- Why Control not Configuration: no existing knob decodes exit-137/no-output;
  this is a new mechanical hook, not a re-tune of an existing processor.
- Retroactive check (A-corrective): yes — the decisive blocker is that at
  msg 5 the agent had no way to know its command was *killed*. Had the first
  `(exit 137, no output captured)` been annotated ("SIGKILLed at the time
  limit; launch services detached with `nohup ... >log 2>&1 & disown` and probe
  in a separate command"), the agent had `Bash` and the scripts already written
  — a detached launch + log read is the working path and would have unblocked
  the 13-turn wall before budget exhaustion. Even in the worst case where the
  model still fails, the escalating REPEAT nudge prevents the ~26-turn verbatim
  loop, reclaiming budget for the rest of the round.

expected_global_gain: Closes the "opaque SIGKILL/timeout → verbatim retry loop"
  failure mode on service/daemon/port-forward tasks — a recurring high-step
  cluster (this task plus the R1-flagged budget_exceeded loops). Turns an
  uninformative wall into an actionable, recoverable signal, and caps wasted
  turns even when the task stays unsolved.

regression_risk: Low. The `on_after_tool` fast path returns immediately unless
  the result contains both `no output captured` AND a kill exit code — a shape
  that never appears on clean/normal output, so passing short-step tasks are
  untouched. Nudges are capped (max 4/task) and only escalate on *consecutive*
  kills; a single isolated timeout on a progressing task costs one short user
  message. before-model injection is contract-safe (rewrites a trailing user
  message instead of +2 insert). No currently-passing cluster emits this shape.

cost_shift: Net negative to neutral. On the common path: zero added tokens.
  On killed-loop tasks: replaces ~26 wasted verbatim turns with an early
  redirect (or clean recovery), strongly reducing tokens on the worst
  offenders. Worst case per task: at most 4 short injected messages.

rollback_trigger: Revert if any currently-passing task regresses (pass_rate
  drops), or if a passing task begins emitting the injected nudge and then
  fails, or if `killed_output` false-positives appear on non-service tasks.
