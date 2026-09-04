# Candidates — Round R1 (tmax-coev-rep12-i2)

Baseline: 20/50 pass (0.40). Two domains shut out: scientific_computing
(0/5), security (0/6). Dominant failure shapes:
- `budget_exceeded` at the 80-step cap (~12 tasks) — agent thrashes in
  repetition loops on genuinely hard crypto / reverse-engineering /
  multi-service tasks (model capability gaps; see NEEDS_FROM_HUMAN notes
  in journal).
- Verifier aborts at **pytest collection** with `ModuleNotFoundError`
  (4 tasks) — a harness-shaped failure independent of solution quality.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Revise the existing `VerifierDepGuardProcessor` to fire **once early**
(first model response) instead of only on the agent's clean exit-intent
turn, and expand its dependency allow-list to the ubiquitous verifier
libraries (`requests`, `imageio`, `PIL`/Pillow, `numpy`, `scipy`, `pyyaml`).

- Tasks affected (verifier collection ImportError, mechanism identical):
  - task_000773_9bc1b20e — `import imageio.v3` → `No module named 'imageio'`;
    agent finished cleanly, guard fired, but `imageio` was NOT in the
    allow-list, so the verifier still aborted at collection.
  - task_000796_828a72cf — `import requests` → collection abort;
    `exit_reason=error`, so the old exit-only guard **never fired**
    (no `VERIFIER DEP CHECK` marker in messages).
  - task_000910_16cc0daf — `import requests` → collection abort;
    `exit_reason=budget_exceeded` (80-step cap), guard never fired.
  - task_002108_a8cfbf2a — `import requests` → collection abort;
    `exit_reason=budget_exceeded`, guard never fired.
- Signal: `final_pytest.output_tail` contains
  `ImportError while importing test module ... ModuleNotFoundError: No
  module named '<mod>' ... Interrupted: 1 error during collection` on all
  four; `grep "VERIFIER DEP CHECK"` shows the old guard fired on the 36
  clean-exit tasks but on **none** of the 3 non-clean-exit ImportError
  tasks.
- Verified (body-quoted):
  - task_000773 final_pytest tail: `/tmp/test_final_state.py:8: in <module>
    import imageio.v3 as iio / E ModuleNotFoundError: No module named
    'imageio'`; last assistant: "The dependencies are now installed. Let me
    do a final verification..." (agent believed it was done — guard fired
    but wrong allow-list).
  - task_000796 result.json: `"exit_reason": "error"`; final tail
    `import requests / ModuleNotFoundError`; no dep-check marker in
    messages → guard silent on error exits.
  - task_000910 / task_002108 result.json: `"exit_reason":
    "budget_exceeded"`, `steps: 80`; final tail `import requests /
    ModuleNotFoundError`; no dep-check marker → guard silent on step-cap
    exits.
- Why Control not Instruction: the missing dependency is a property of the
  hidden verifier, never mentioned in the task text — no prompt rule can
  make the agent reliably infer "the grader will `import imageio`". This
  is a mechanical guard that must fire uniformly regardless of the agent's
  reasoning or exit path, which is exactly a cross-task `MultiHookProcessor`
  responsibility.
- Why Control not Action: no new agent action space is needed — the fix is
  a forced, deterministic Bash call injected by the harness, not a tool the
  model chooses to call.
- Retroactive check (A-corrective): partial-yes. task_000773 was a clean
  finish blocked *only* at collection by a missing allow-list entry —
  adding `imageio` + firing reliably would let the verifier run (highest-
  confidence flip). task_000796/000910/002108 were incomplete/thrashing
  solutions, so the install alone may not flip them this round — but firing
  early guarantees the dep is present for **any** correct-but-non-clean-exit
  solution on future rounds, which the old guard structurally could not do.
  Net: closes a real class of `reward=0` on otherwise-complete solutions.

- expected_global_gain: recovers the "correct solution, uncollectable
  verifier" class. Concretely flips task_000773-shaped tasks; generalizes
  to every future task whose verifier imports one of the six common libs
  and whose agent exits via step-cap/error rather than cleanly. Spans
  security + system_administration + file_operations domains (the four
  affected tasks cross three domains).
- regression_risk: LOW. The install is fully `|| true`-guarded and a no-op
  when modules already present (36 clean-exit tasks already ran the old
  install with zero observed harm). The only behavioural change is one
  extra Bash tool call appended to the agent's FIRST response; it runs
  after the agent's own first call, does not alter the agent's messages,
  and cannot fail the run. Worst case: a few seconds of pip time on images
  that lack a package.
- cost_shift: +1 Bash round-trip per task (was already ~1 round-trip on
  clean-exit tasks under the old guard; now uniformly one, earlier). The
  install output is short (six `ok:`/`installed:` lines). Negligible token
  increase; no change to model output caps.
