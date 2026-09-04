# Candidates — R2 / c2

Assigned focus: `task_000106_23215092` (data_querying) fails with reward=0.

## Candidate C-002

**Three-axis tag:** lens=structural-sandbox-topology / lever=control /
intent=broaden-existing-processor-trigger (widen the recall of the R1
`VerifierHttpDepReminder` so it actually fires on the failure class it was
built for).

### Signal

`task_000106_23215092.result.json`: `reward=0`, `final_pytest.passed=false`,
`rc=2`, output tail:

```
ImportError while importing test module '/tmp/test_final_state.py'.
E   ModuleNotFoundError: No module named 'requests'
Interrupted: 1 error during collection
```

The agent built a **correct** Flask API on `127.0.0.1:8000` (verified in the
message log: correct co-authorship SQL self-join, networkx PageRank, oracle
subprocess, 404 handling) and exited cleanly (`finished=no_tool_calls`,
39 steps). It scored 0 purely because the external verifier's
`test_final_state.py` could not `import requests`.

This is the **exact** failure the R1 `VerifierHttpDepReminder` was authored to
prevent — but the R1 reminder **never fired** on this task (verified:
`"EXTERNAL automated verifier" not in messages`). Root cause: R1's
`_VERIFIER_RE` demands explicit *verifier*-family vocabulary
("automated verifier", "verifier will make requests"). This task instead says
*"leave your API running ... so that our automated integration tests can query
it to verify your work."* — no match.

### Verified body evidence (regex replay across all 50 trajectories)

Four tasks fail on `ModuleNotFoundError: requests` (all reward=0):
`task_000106` (data_querying, Flask), `task_000809` (data_processing, Python
HTTP), `task_000958` (data_querying, C++ microservice), `task_002063`
(software_engineering, Rust-extension Python server).

- R1 `_VERIFIER_RE ∧ _SERVICE_RE` fires on **0 / 4** of these reqfail tasks.
- The widened `_PROBER_RE ∧ _SERVICE_RE` fires on **4 / 4**.
- Overall it fires on 11 of the 14 service-shaped tasks; 3 of those 11 are
  already-passing tasks. Reminder is advisory + idempotent (`import requests`
  is a fast no-op if present; `pip3 install` is idempotent) so it cannot
  convert a pass into a fail.

None of `task_000958` / `task_002063` used any verifier vocabulary at all —
they say *"server runs continuously ... to accept traffic"* and *"Leave the
server running in the background"*. Those "leave it running for an external
prober" phrasings are the generalisable structural marker, so they were added
to the widened trigger.

### Change

Replace R1's narrow `_VERIFIER_RE` with a broadened `_PROBER_RE` inside a new
copy of the processor under this candidate's `processors/` dir. `_SERVICE_RE`
(the strict "is this actually a network service" gate) is unchanged, so pure
file/data/query tasks stay untouched. Config repoints the `_target_` `file://`
path to the new copy; `dep_module: requests` knob unchanged.

### Retroactive check (variant: would-it-have-fired)

For the assigned task and all 3 sibling reqfail tasks: **partial-to-strong
yes.** The reminder would now fire and deliver the missing knowledge
("`import requests` must be importable in system Python before you exit"). It
does not *guarantee* the agent installs it, but it converts a 0% chance
(reminder silent) into a live chance, and outbound pip is confirmed working in
this environment. Strongest on `task_000106`/`task_000809`/`task_000958` where
the service was otherwise correct — the requests import was the *only* thing
between them and reward=1.

### Why control (broaden trigger) not another lever

- Not **instruction** (system prompt): the knowledge is a runtime,
  task-shape-conditional fact ("this specific service will be probed by a
  Python test that needs requests"). Baking it into the global prompt would
  spam every non-service task and violate the "no task-specific literals /
  general strategy only" rule. A gated processor delivers it exactly when the
  structural signal is present.
- Not a **new processor**: the mechanism already exists and is correct; the
  defect is pure recall (the trigger under-matched). Widening the regex is the
  minimal, lowest-risk edit.
- Not **configuration knob tuning**: the failure is not a threshold; it is a
  missing match on real phrasings.

### Pareto statement

- `expected_global_gain`: the build-a-service-the-prober-calls cluster —
  4 tasks currently hard-failing on `ModuleNotFoundError: requests` regardless
  of service correctness. Generalises to any future task that stands up a
  network service and leaves it running for an external Python prober.
- `regression_risk`: fires on 3 already-passing service tasks, but the injected
  message is advisory and the install is idempotent, so it cannot flip a pass
  to a fail. Marginal cost is a few tokens + at most one `pip3 install requests`
  per fired task. `_SERVICE_RE` unchanged ⇒ no new fires on non-service tasks.
- `cost_shift`: small positive on ~11 service tasks (one extra reminder message
  + possibly one install command); ~0 on the other 39 tasks.
- `rollback_trigger`: if next round shows the 4 reqfail tasks still failing on
  `requests` (agent ignores the reminder) AND service-task mean step-count
  materially up, revert to the R1 narrow trigger — the mechanism would then be
  a model-compliance gap, not a harness recall gap.
