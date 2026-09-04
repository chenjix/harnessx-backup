# Candidates — R2 / c0 (focus: task_000010_644ab1c2)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `BackgroundServiceHygieneProcessor`: a churn-gated, one-shot strategy
nudge that fires when the tool layer emits background-service lifecycle
failure signatures (`command timed out after Ns` and/or `address already in
use`), telling the agent to free stale ports, make its listener durable, and
probe with a bounded timeout instead of re-running a hung command.

- Tasks affected (primary): task_000010_644ab1c2. Same tool-layer signature
  class also burns budget on task_000396_e56917e2 and task_001536_acfe6c35
  (each 1x `command timed out after` foreground hang), so the trigger is a
  recurring cross-task shape, not a one-task regex.
- Signal: tool-result strings `command timed out after` (foreground command
  blocked to the Bash timeout against a non-reachable service) and `address
  already in use` (bind collision with a stale, un-reaped background PID).
  task_000010 result.json: `exit_reason=budget_exceeded`, `elapsed_s=1113`,
  80 steps; final_pytest `test_api_success_log` shows only the ConfigMap was
  ever applied (deploy-v2.yaml never got through the flaky proxy).
- Verified (Read of task_000010_644ab1c2.messages.json):
  - msg 18/28/69 tool result: `socketserver.TCPServer(("127.0.0.1", 8080))`
    Traceback — port 8080 held by a stale mock_api the agent never reaped;
    agent misreads it (msg 19/29) as "the mock API is still running" and
    moves on.
  - msg 32, 46 tool result: `Error: command timed out after 120s` — the
    interactive CLI hung against the non-durable port-forward for the full
    120s (3 such hangs = ~360s of the 1113s run wasted).
  - msg 65 tool result: `socat[801] E bind(...) Address already in use` on
    :9090 — same stale-listener collision on the forward port.
  - Cross-task: task_000396_e56917e2 and task_001536_acfe6c35 each have one
    `command timed out after` tool result (same foreground-hang signature).
- Why Control not Instruction: the failure is a mechanical tool-layer signal
  (timeout / bind error strings) the agent repeatedly *misdiagnoses in the
  moment*; an always-on prompt rule is read at task start and forgotten by
  the time the churn starts 20+ steps in. A Control hook keyed on the exact
  failure strings delivers the lifecycle discipline at the moment the
  signature appears, to every task that exhibits it, and to none that don't.
- Why Control not Action: the Bash tool already surfaces the failure (timeout
  message, bind traceback) — nothing needs a new capability; the gap is that
  the agent doesn't act on that return. A new tool would duplicate Bash and
  cannot change Bash's fixed 120s timeout anyway (TB2 tool set is frozen).
- Retroactive check (A-corrective): yes — had the nudge fired after the 2nd
  churn signature (well before step 30), the agent would have been directed
  to reap the stale :8080/:9090 listeners and stand up a durable multi-conn
  proxy + bounded probe, instead of spending the remaining ~50 steps and
  ~700s cycling kill/restart/hang. The remaining path failure (wrong operator
  filename) is owned by the sibling completion-discipline candidate, not this
  one; this candidate targets the budget-burn root that prevented the run
  from ever reaching a clean exit.
- expected_global_gain: Flips / de-risks the background-service churn slice of
  the budget_exceeded cluster (task_000010 is the archetype) and trims wasted
  wall-clock on any port-forward / mock-API / socket-server task by breaking
  the hung-command + stale-port-rebind loop early. Generalizes to any task
  producing the two generic OS/tooling failure strings.
- regression_risk: Low. Fires at most once per task and only after >=2 churn
  signatures; a task with a single legitimate transient timeout never trips
  it. Injects one user message, no tool round-trip, changes no control flow,
  new singleton group (adds a processor, replaces nothing). Worst case on a
  false positive: one extra ~250-token advisory message on a task that hit
  two timeouts for unrelated reasons — cheap and non-destructive.
- cost_shift: Net negative on the affected cluster (churning tasks stop
  burning 120s hangs and budget-exhausting restart loops far earlier).
  +~250 tokens once on any task that crosses the threshold; zero on healthy
  runs (no signatures -> never arms).
