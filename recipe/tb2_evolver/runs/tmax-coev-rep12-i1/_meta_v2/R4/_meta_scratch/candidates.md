# Candidates — R4 (tmax-coev-rep12-i1)

Incumbent R3 = 26/50. Score stuck ~25-29 across rounds. R3's
verifier_dep_guard is accepted and KEPT (it let task_001857 progress
past the `import requests` collection abort). This round targets a
different lever: the system-prompt sidecar, which is currently the
5-line default and encodes none of the workflow discipline the
playbook flags as the biggest score lever on terminal-bench tasks.

## Candidate C-001
[lens: success | lever: instruction | intent: preservative-transfer]

Promote an explicit general workflow — survey → restate deliverables &
plan → diagnose-before-retry → budget awareness → verify-as-a-checker-
would → keep services alive → don't pollute the working dir — from an
implicit habit of the fast passing tasks into an explicit rule applied
to all tasks, via the system-prompt sidecar.

- Tasks affected:
  - passing (habit fired — short, structured, verified): task_000225_1391f954
    (91s), task_000404_e2591ca5 (52s), task_000837_f8e45558 (78s),
    task_000741_7786c14b (30s) — these finish fast with a clear
    inspect→act→confirm arc.
  - failing (habit absent at the decisive step): task_000470_f819ab03
    (blind retry loop), task_000716_206dc0f6 (1280s thrash),
    task_000506_c13429e7 / task_001378_3143a60f (trial-and-error debugging
    to budget), task_001857_24daeef3 (verified its server with a lenient
    raw-socket read that hid a malformed HTTP framing bug).
- Signal: longest FAILING tasks all show `elapsed_s` at/near the ceiling
  with ~33 assistant messages (budget-bound) and bodies dominated by
  "Let me try a different approach" without root-cause analysis; the
  fastest PASSING tasks finish in 30-90s. task_001857 pytest tail:
  `BadStatusLine('{"status": "healthy"}')` — its own final probe used a
  raw socket and reported `Match: True`, but the verifier's HTTP client
  rejected the malformed response.
- Verified (Read):
  - task_001857_24daeef3 final steps: agent runs a raw `socket` probe,
    prints `Actual: {"status": "healthy"}` / `Match: True`, declares done;
    verifier `requests.get(...)` fails with `BadStatusLine`. Lenient
    self-check masked a protocol-framing bug an HTTP client would catch.
  - task_000470_f819ab03 final steps: 4x byte-identical
    `echo "..." | /app/metrics_extractor 2>&1; echo "Exit: $?"` each
    prefaced by "I've been stuck in a loop" — repeats the same probe
    instead of diagnosing why the service isn't listening.
  - task_000716_206dc0f6 (1280s), task_000506_c13429e7 (629s),
    task_001378_3143a60f (662s): first assistant message is already
    mid-trial-and-error ("Let me try a different approach", "test
    different tie-breaking scenarios") with no plan/deliverable list;
    burn ~33 turns iterating to budget.
  - passing task_000404_e2591ca5 / task_000225_1391f954: finish in
    <95s with a compact inspect→act→confirm sequence.
- Why Instruction not Control: R1 already tried a Control hook
  (NoProgressRepeatGuard) that mechanically detected identical-command
  loops and was REVERTED (net -4). A mechanical injector can flag a loop
  but cannot supply the missing behaviour — root-cause diagnosis, a
  correct HTTP-client verification, output-path confirmation — which is a
  reasoning discipline the agent must apply itself. The scoping (which
  deliverables, which verification interface) has to stay agent-authored;
  a hook that injects a fixed plan bypasses that. So the fix belongs in
  the prompt, not another guard.
- Retroactive check (C-preservative-transfer): yes — task_001857 would
  pass if the agent verified its endpoint with a real HTTP client (curl)
  instead of a raw socket, catching the missing status line before exit;
  the thrash cluster (000716/000506/001378/000470) begins iterating
  before a plan/root-cause exists, and an up-front survey+plan+diagnose
  discipline is exactly what the fast passing cluster does implicitly.
- expected_global_gain: Targets two failing sub-clusters at once — the
  budget-thrash/blind-retry cluster (>=4 tasks) and the lenient-self-
  verification cluster (task_001857 cleanly; generalizes to any HTTP/TCP
  service task) — while re-encoding the discipline the passing cluster
  already relies on so a future prompt edit cannot silently regress it.
- regression_risk: Prompt is longer, so the model could over-invest in
  planning/verification on trivial tasks and spend a few extra steps; all
  guidance is general (no task literals), and it never mandates copying
  code or a specific algorithm, so it cannot inject a wrong solution. Main
  risk is mild verbosity cost on already-passing short tasks.
- cost_shift: +a few hundred prompt tokens per task (one-time system
  prompt) and possibly +1-2 verification commands on service tasks;
  expected to be net-neutral-to-negative overall because it should cut
  the multi-hundred-second thrash loops (000716 at 1280s, 000348 at 845s)
  that currently burn the most budget.
