# Candidates — R1/c7 (focus: task_000010_644ab1c2 + task_000015_89886d8d)

## Diagnosis summary

Assigned cluster = two failing tasks with *distinct proximate* causes but one
shared harness-actionable root:

- **task_000015_89886d8d** — `exit_reason=done` (`no_tool_calls`, 37 steps),
  `final_pytest` accuracy **0.3389 < 0.98**. The task said a hidden test suite
  would run the script on "a massive, hidden dataset of edge-case URLs". OCR of
  `/app/routing_schema.png` was noisy but legible enough to show the target
  schema keys (`product_id … category … order`, `session_token … traffic_source`,
  `account_id … theme … notifications`). The agent IGNORED the OCR target column
  and instead named its output keys after the *sample's own query params*
  (`dept→department`, `sort→sort_order` — should be `→category`, `→order`). It
  then verified only that its script *ran* on the 3-line sample and stopped.
  Proximate cause: fit-to-sample + wrong key derivation. (Body msg 37 shows the
  ROUTES table with the wrong mappings; msgs 2/4/12/16/20/24 show the OCR target
  column carrying `category`/`order`.)

- **task_000010_644ab1c2** — `exit_reason=budget_exceeded` at 80 steps. Wrote
  deliverable to `/home/user/k8s_operator.py` while the verifier required the
  EXACT path `/home/user/operator.py`; also never got the base mock API running.
  Proximate causes: truncation spiral (already the focus of R1's sibling
  candidate C-001/C-002) + wrong exact path. (Body: `final_pytest` "Operator
  script /home/user/operator.py does not exist".)

Shared harness-actionable root: **the agent finishes after a shallow check
(script ran / sample matched / file plausibly exists) without re-deriving the
EXACT output contract — key names, exact path/name, format, accuracy behaviour
— from the authoritative source, and without stress-testing beyond the sample.**

The truncation-spiral half of task_000010 is owned by the sibling proposal
(h_length_recovery_forceact_v1); this candidate deliberately does NOT duplicate
that lever and instead ships the complementary completion-discipline mechanism.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `RigorousCompletionGuard`: a bounded, self-advertised-rigor-gated extra
pre-exit verification hook that, on the agent's exit-intent turn, injects a
targeted reminder to (a) re-derive the exact output contract from the
authoritative source rather than guessing from samples, and (b) stress-test
edge cases before finishing. Fires at most `max_fires=2` times per task.

- Tasks affected: task_000015_89886d8d, task_000010_644ab1c2
- Signal: task_000015 `exit_reason=done` yet `final_pytest` accuracy 0.3389;
  task_000010 wrong-exact-path `final_pytest` failure. Both tasks' descriptions
  carry rigor cues ("massive, hidden dataset", "0.98 threshold", "edge-case",
  "robust", "property-based" for 015; exact-path spec for 010) that the
  `_RIGOR_CUES` regex matches.
- Verified (Read):
  - task_000015 msg 37 (Bash `cat > migrate.py`): ROUTES table maps
    `dept→department`, `sort→sort_order` — comments literally derive keys from
    the query-param names. msgs 40 & 66: agent's only correctness check is
    running on the 3-line `sample_urls.txt`. msg 73 final: "All tests pass and
    the script works correctly with the sample URLs." → declared done on a
    shallow sample check. OCR msgs 4/16/24 show the true targets `category` /
    `order` / `traffic_source` were in context and ignored.
  - task_000010 result.json `final_pytest`: `Operator script /home/user/operator.py
    does not exist` — wrote `k8s_operator.py`; agent never re-confirmed the exact
    required filename from the task text before the budget ran out.
- Why Control not Instruction: the existing `SiblingSystemPromptBuilder` prompt
  already says "verify what you did"; a static prompt line is read once at t0 and
  is exactly what both agents narrated past. The failure is *timing* — the nudge
  must fire mechanically at the exit boundary, conditioned on runtime state (the
  agent is trying to stop) and on the task text. That is a hook, not a static
  rule. Instruction also cannot gate on "task advertises a hidden eval", which is
  what keeps this from firing on trivial already-passing tasks.
- Why Control not a knob on the existing `CustomSelfVerifyProcessor`: that
  processor fires a *generic* checklist *exactly once*; both agents passed
  through an equivalent one-shot nudge and still shipped a shallow-verified
  wrong answer. This guard is narrower (gated on rigor cues) and bounded-repeat,
  targeting the specific "fit-to-sample / wrong-exact-contract" failure the
  generic one-shot did not catch. Re-parameterising the builtin can't add a
  content-specific, rigor-gated, repeatable nudge.
- Retroactive check (A-corrective): yes, with caveat.
  - task_000015: the injected guidance directly targets the error made ("do NOT
    infer names from the sample's own field names — extract them from the spec";
    "if OCR was noisy, re-run and reconcile"). The correct keys were already in
    the agent's OCR context, so a redirect to trust the source over the sample
    is a plausible flip. Caveat: this remains partly a model reasoning/OCR
    capability limit — the nudge raises the odds, it does not guarantee the flip.
  - task_000010: the "confirm every required deliverable is at its EXACT path/
    name as written in the task" step would surface the `operator.py` vs
    `k8s_operator.py` mismatch at the exit boundary — but only if the agent
    reaches an exit turn before `budget_exceeded`; the truncation-spiral half
    (sibling C-001) has to hold first for this to bite. Net: this candidate is
    the second layer for 010, primary layer for 015.

- expected_global_gain: Flips/derisks the "declared done after shallow check"
  failure shape, which recurs across the sample beyond these two tasks (any task
  advertising a hidden/accuracy-graded eval — OCR-schema, data-migration,
  service-contract). Generalises via generic rigor-cue vocabulary + pure-strategy
  guidance; no task literals.
- regression_risk: Low. The guard is (1) gated — it only arms when the task text
  matches rigor cues, so trivial already-passing tasks see no extra turn; (2)
  bounded — `max_fires=2`, so it cannot cause an exit-block/budget spiral (the
  common failure mode of naive "block completion" hooks); (3) additive and
  message-contract-safe (+1 user per fire, verified by the auto contract check),
  ordered `_order=91` right after the existing one-shot verifier. Worst case on a
  wrongly-armed task: up to 2 extra verification turns, then normal exit.
- cost_shift: +0 on non-armed tasks. On armed tasks, +1–2 short verification
  round-trips plus whatever extra checking the agent does — a small token
  increase, expected to be net-positive when it converts a 0-reward run into a
  pass or prevents wasted downstream retries.
