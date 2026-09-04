# Candidates — R2 c6 (focus: task_000140_01c78b42)

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Append a two-sided service end-state item (keep-alive vs. teardown) to the
stock self-verify checklist so lifecycle / init-script / CI-pipeline tasks
whose verifier requires a clean shutdown are no longer silently steered
toward leaving lingering processes.

- Tasks affected (mechanism = verifier asserts clean teardown, agent nudged
  only toward keep-alive):
  - primary flip: task_000140_01c78b42 (only literal `pgrep`-lingering
    failure in R0)
  - broader cluster protected/nudged: the service/server tasks that reach
    the self-verify checkpoint and start background processes during their
    own testing — task_000028_7fe033ac (Nginx + C++ socket backend),
    task_000010_644ab1c2 (K8s operator w/ port-forward), plus any unseen
    lifecycle task. Only task_000140's verifier happens to assert teardown
    in R0, so the *measurable* corrective flip is 1; the mechanism it fixes
    (one-sided checklist bias) is general.

- Signal: `final_pytest` on task_000140 fails ONLY
  `test_no_lingering_service_processes`:
  `AssertionError: Lingering vm_service processes found: ['340','592']` /
  `['340','592','787']`; the other 2 tests pass; `initial_pytest.passed=true`.
  The stock self-verify checklist (harness.py `_SELF_VERIFY_MSG`, item 5) is
  one-sided: it only ever says "confirm they are still alive and reachable",
  never "tear down what you started".

- Verified (Read):
  - task_000140 message 0 (task text): "**Construct a CI/CD Test Pipeline** …
    Finally, it must **gracefully stop** the Go service by reading
    `/home/user/service.pid` and sending a SIGTERM." — explicit teardown
    requirement.
  - task_000140 messages step 15: agent runs `test_pipeline.sh` during its
    OWN agent phase (curl returns 200), which starts a `vm_service`; the
    pipeline's `kill -TERM $PID` is issued without a `wait`/re-check.
  - task_000140 step 21: `Verification check initiated. See the message above`
    — the stock self-verify fired. Steps 22-24: the agent re-reads the task
    and re-`ls`es files but issues NO process check and NO teardown; it exits
    with strays alive → verifier `pgrep -f vm_service` finds them.
  - harness.py:96 confirms item 5 is keep-alive-only; harness.py:381 (system
    prompt) likewise only says "keep them running after you exit". Nothing in
    the read-only harness ever nudges teardown.

- Why Control not Instruction: the corrective nudge must fire at the exact
  decisive checkpoint (the once-per-task self-verify pass, right before exit)
  and must augment the *existing* injected checklist text that lives in
  read-only `harness.py`. A SiblingSystemPromptBuilder / template edit
  (Instruction) would add the guidance to the persistent system prompt, where
  it competes with harness.py:381's contradictory keep-alive rule and is far
  from the exit moment; a processor that edits the last-user self-verify
  message places the balancing item adjacent to the biased item 5, at the
  moment the agent is actually deciding whether to exit. It also fires exactly
  once and only when the checklist is present, so non-service tasks are
  untouched — an Instruction rule pays that surface on every task/turn.

- Why Control not Configuration: CustomSelfVerifyProcessor exposes no knob for
  its message text; there is no existing parameter to retune. The one-sided
  bias is baked into read-only harness.py, so a new hook is required.

- Retroactive check (A-corrective): yes — on task_000140 the self-verify
  checklist DID fire (step 21) and the agent re-verified but never tore down,
  because item 5 only ever pointed toward keep-alive. Had the two-sided item
  been in that same checklist message, the "match the required end state /
  torn down -> stop AND verify none survive" branch applies directly to a task
  whose text literally says "gracefully stop … no lingering", giving the agent
  the missing nudge to run a `pgrep`/reap-and-recheck before exit. The
  capability (Bash pkill/pgrep/wait) is fully present; the missing thing was
  the exit-time steering, which this supplies.

- expected_global_gain: Flips the lingering-process task and, more durably,
  de-biases the once-per-task self-verify checkpoint so the entire
  service/lifecycle cluster is no longer steered against any clean-teardown
  criterion. Generalizes to unseen lifecycle/init/CI tasks with zero
  task-specific literals.

- regression_risk: Low and explicitly two-sided. The added item first tells
  the agent to KEEP a service alive when the verifier connects to it (mirrors
  the existing correct behaviour), so it cannot push keep-alive tasks toward
  wrongly killing a needed service. It edits only the last user message and
  only when that message is the self-verify checklist (sentinel-gated),
  inserts no new message (contract-clean: last=user content edit), and fires
  <=1x/task. Non-service tasks that reach self-verify get ~10 extra lines of
  end-state reasoning they will read as inapplicable and skip.

- cost_shift: Negligible: ~120 tokens appended to a single already-injected
  checklist message on tasks that reach the exit checkpoint; may prompt 1-2
  short teardown/keep-alive verification Bash calls on genuine lifecycle
  tasks. No forced extra model turns; zero cost on tasks that never self-
  verify.

- rollback_trigger: Revert if any previously-passing service/server task
  regresses to F attributable to the agent killing a service the verifier
  needed alive, or if synthetic replay fails on SelfVerifyTeardownBalance.

### Why not the sibling's launch-command-triggered reminder shape
A batch sibling (h_service_lifecycle_reminder_v1) targeted the same task with
an `on_after_tool` reminder gated on a background-LAUNCH regex. That shape
misses tasks that leak processes without matching the launch pattern (e.g. a
pipeline the agent invokes with plain `bash script.sh`, as task_000140 does at
step 15 — no `&`/`nohup`/`systemctl` in the invoking command). This candidate
attaches the balancing item to the universal self-verify checkpoint instead,
so it fires at exit regardless of how the process was started — a strictly
broader, launch-pattern-independent trigger.
