## Round 2 — killed-output diagnostic

<!-- journal:frontmatter
round: 2
timestamp: 2026-06-02T00:00:00Z
hypothesis_id: h_killed_output_diagnostic_v1
levers: [control]
predicted_affected: [task_000010_644ab1c2, task_001321_658ce4a8, task_000958_4bb2b05d, task_001031_a8f0eb37]
cited_candidates: [C-001]
gating_outcome: pending
gating_attribution: pending
expected_global_gain: "Closes the opaque SIGKILL/timeout -> verbatim retry loop failure mode on long-running-service / daemon / port-forward tasks; turns the uninformative (exit 137, no output captured) wall into a recoverable, actionable signal and caps wasted turns even when the task stays unsolved"
regression_risk: "Low. on_after_tool fast-path returns unless the result contains BOTH the no-output marker AND a kill exit code (137/143/124/152) — a shape absent from clean output, so short-step passing tasks are untouched. Nudges capped at 4 per task, only escalate on consecutive kills; before-model injection is contract-safe"
cost_shift: "Net negative to neutral: zero added tokens on the common path; replaces ~26 wasted verbatim turns on killed-loop tasks with an early redirect/recovery"
rollback_trigger: "Revert if pass_rate drops, if a previously-passing task begins emitting the nudge and then fails, or if the killed-output shape false-positives on non-service tasks"
-->

### Why

Assigned task `task_000010_644ab1c2` (write a Python k8s operator that backs up
manifests, sets up a 9090->8080 port-forward, and drives an interactive CLI)
failed budget_exceeded at 80 steps with reward 0. The trajectory is a
non-converging loop: the agent runs the byte-identical command
`python3 /home/user/port_forward.py &; sleep 2; <port check>` ~13 times and each
time receives the single opaque line `(exit 137, no output captured)`. Exit 137
= 128 + SIGKILL(9): the sandbox wraps every command as
`setsid bash -c <cmd> & ...; wait $_hx_pid` with a 30s per-command timeout
(`benchmarks/terminal_bench_2/harbor_sandbox.py` lines 84-114), and the agent's
launch-then-test-in-one-command pattern blocks on `wait`, gets SIGKILLed at 30s,
and returns nothing. The model reads the empty result as "port not listening
yet" and reruns verbatim until budget is exhausted. This is a harness
deficiency, not a knowledge gap: the sandbox strips the one fact the agent needs
(the command was killed). The same opaque-kill-then-retry shape underlies the
R1-flagged high-step budget_exceeded loops (task_001321, task_000958,
task_001031).

### Changes

- `processors/killed_output_diagnostic.py` — new `KilledOutputDiagnosticProcessor`
  (MultiHookProcessor). `on_after_tool` classifies each Bash result: if it
  contains the no-output marker AND a kill exit code (137/143/124/152), it flags
  a pending nudge and tracks the consecutive-kill streak; a non-killed result
  resets the streak. `on_before_model` injects a one-shot (escalating at >=2
  consecutive) actionable diagnostic — explains SIGKILL/timeout semantics and a
  general strategy for launching persistent services detached with output
  capture (nohup redirect + disown) and probing in a separate command.
  Capped at max_nudges=4 per task; contract-safe trailing-user merge.
- `config.yaml` — register the processor (file:// abs path) after
  LengthTruncationRecoveryProcessor (order 6) and before CompactionProcessor.

### Evidence

- `task_000010_644ab1c2` result.json: reward=0, steps=80, exit_reason=budget_exceeded.
- messages.json msg 4 command starts a port_forward with a bare `&`, sleeps,
  then checks port 9090; msg 5 result = `(exit 137, no output captured)`.
- msg 43 command byte-identical to msg 4; msg 44 result identical. msg 56 again.
  ~13 identical `(exit 137, no output captured)` results across msgs 5..57.
- Root cause: `harbor_sandbox.py` lines 110-111 emit `(exit {rc}, no output
  captured)` after the wrapped command is SIGKILLed at the 30s timeout.

### Uncertainty

The nudge relies on the model acting on the redirect (launch detached + read
log) rather than ignoring it as it ignored the passive re-runs. If the model
still cannot make the service start non-blocking, the task may stay failed — but
the escalating REPEAT nudge and the cap prevent the ~26-turn verbatim loop,
reclaiming budget for the rest of the round. Watch for any passing task emitting
the injected nudge (would indicate a false-positive on the killed-output shape).
