# Candidates — R2 / c0

Assigned focus: `task_000010_644ab1c2` (create `/home/user/operator.py`).

## Diagnosis (why R1's fix is not enough)

R1 shipped `LengthLoopBreaker` to close a content-only max_tokens loop and it
worked: this trajectory now exits cleanly (`exit_reason=done`, 24 steps,
`finished=no_tool_calls`) — the runaway narration loop is gone. But the task
STILL fails, and the failure is now a different, generalizable shape.

The agent built a fully working operator script. The grader's only failing test
is `test_operator_script_exists` — `/home/user/operator.py` does not exist; the
other 2 tests already pass. The agent had relocated the deliverable to
`/home/user/k8s_operator.py` because naming an *actively imported* script
`operator.py` shadows the stdlib `operator` module **when run from that cwd**.
It then ran the existing generic `CustomSelfVerify` checklist, `ls`-ed its OWN
chosen path (`k8s_operator.py`), confirmed "file exists", and exited satisfied.

This is the tb2-playbook "Correct logic, wrong path" structural failure mode.
The generic self-verify checklist is prose the agent satisfies against a path of
its own choosing — it is never grounded against the paths the task actually
demands.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `DeliverablePathGuard`: on an exit-intent turn, extract the absolute file
paths named in the task description and block the exit once (up to `max_blocks`)
if any of them do not exist on disk, naming the SPECIFIC missing required paths.

- Tasks affected: `task_000010_644ab1c2` (archetype — file at wrong path).
  Same missing-required-file mechanism recurs across the fail set whose FIRST
  grader assertion is a missing-path check: `task_000933_1f27096a`,
  `task_000321_ad43caad`, `task_000111_cbada64a`, `task_000118_3043e92d`,
  `task_000264_ab8c7253`, `task_001547_8cde5da2`, `task_001090_c61c71f2`,
  `task_000635_64d4f731`, `task_000396_e56917e2`, `task_000760_e197f7ff`,
  `task_000748_c9807703` (12 tasks total flagged by
  `final_pytest.output_tail` first assertion = `os.path.isfile/exists ...
  does not exist / missing`). Not all are wrong-path (some are genuine
  never-produced), but for all of them the guard would have surfaced the exact
  missing path before exit rather than letting the agent finish blind.
- Signal: `agent.finished=no_tool_calls`, `exit_reason=done`, `reward=0`, and
  `final_pytest.output_tail` opens with an `os.path.isfile/exists` assertion on
  a path that is explicitly named in the task prompt.
- Verified (Read):
  - task_000010 msg[0] prompt: "write a Python script at
    `/home/user/operator.py`". msg[17] agent renames it to `k8s_operator.py`
    after a self-shadow import error; msg[45-47] runs `ls` on
    `k8s_operator.py`, declares "All requirements are met", exits. result.json
    `final_pytest`: only `test_operator_script_exists` fails
    (`/home/user/operator.py does not exist`), 2/3 pass.
  - task_000933 msg[last]: agent asserts all files created, yet
    `final_pytest` first failure is `isfile(...) ... is missing`.
  - task_000321 msg[last]: "✅ Longest uptime written ..." yet first grader
    failure is `os.path.exists(deploy_dir) ... does not exist`.
- Why Control not Instruction: an Instruction/prompt rule is exactly what the
  existing `CustomSelfVerifyProcessor` already is (a prose "check every required
  output file exists" checklist), and the agent satisfied it against its own
  wrong path and exited anyway — the prompt layer cannot ground the check
  against on-disk truth. Control is the only layer that can `test -e` the
  concrete paths in the live sandbox and block the exit when a stated
  deliverable is genuinely absent. No new capability is missing (Bash suffices),
  so not Action; no existing knob tunes this behaviour, so not Configuration.
- Why Control not just re-tuning CustomSelfVerify: that processor injects static
  text with no path grounding and no block-until-satisfied loop; the fix needs
  runtime sandbox probing + a bounded re-entry, which is new mechanism.
- Retroactive check (A-corrective): yes — task_000010 had a working script one
  `cp k8s_operator.py operator.py` away; a guard naming
  "`/home/user/operator.py` does not exist" at the exit turn gives the agent the
  exact, unambiguous action to flip the one failing test. The message also
  tells it the file only needs to *exist* (not be importable) at that path,
  directly countering the self-imposed constraint that caused the relocation.
- expected_global_gain: closes the "correct logic, wrong/missing path" cluster
  (12 fail-set tasks show a missing-path first assertion). Generalizes because
  paths are derived at runtime from each task's own prompt — no task knowledge
  baked in.
- regression_risk: low. The guard only ever *adds* one extra turn on exit and
  only when a prompt-named path is absent; input files already exist so they
  self-filter. Bounded at `max_blocks=2`, then it goes silent — a genuinely
  impossible deliverable cannot trap the loop. Worst case on a passing task: a
  path the prompt named as optional/alternative is missing → at most 2 extra
  verification turns, no hard stop. Conservative regex (absolute root + file
  extension) avoids matching prose.
- cost_shift: mildly positive to neutral. Adds ≤2 short probe turns on affected
  exits; flips zero→some passes that previously burned a full run for reward 0.

## Why not drift onto another proposal's focus

Staying on the assigned task_000010. The intervention is scoped to the failure
that task now exhibits (missing required deliverable path at clean exit), which
is a real recurring cluster, not a one-off.
