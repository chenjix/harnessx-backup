# Candidates — Round 1 (baseline R0 = 15/50 = 30%)

## Diagnosis summary

R0 failure taxonomy (35 fails / 50):
- **29/35 failures exit `no_tool_calls`=`done`** — agent voluntarily
  declares completion. Only 6/35 hit `budget_exceeded`.
- The `CustomSelfVerifyProcessor` **already fires** on 28/29 of those
  `done`-fails (and on all passing runs). The one-shot verify nudge is
  already present; adding more prompt nudges won't manufacture correctness
  the model can't produce. → most `done`-fails are **model capability gaps**
  (semantically-wrong output that passes an `ls` existence check). No harness
  fix — skip.
- The SUCCESS marker instructed by the verify processor is **never emitted**
  by ANY run (pass or fail) → not a discriminating signal, nothing to lock in.

Genuinely harness-addressable sub-cluster:
- **Exact-command thrash loops.** Several long/budget-exceeded runs re-issue
  the *literally identical* Bash command many times without any state change:
  task_408 `debugfs ... ls -l /` x17, task_1716 python inline x22,
  task_010 `sleep;ps aux|grep` x10, task_015 `pytest` x10, task_118 cleanup x15.
  These burn the entire step budget. The existing `CustomEditToolProcessor`
  only counts file-WRITE commands (redirect/sed -i/tee) — it is blind to
  read/poll/debug loops. This is a real Control gap.

Caveat driving the design: high exact-repeat counts also appear in some
*passing* runs (task_123 echo x16, task_358 x4, task_1035 x5). So the guard
is scoped tightly to avoid regressing them (see C-001 regression_risk).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `RepeatedCommandGuard` processor that counts *exact-identical* Bash
command executions per task and, once one command crosses a high threshold
(5), appends a one-shot "you are looping — the command is not changing state,
take a fundamentally different action" nudge to that tool's result (then
resets the counter so it can re-fire if the loop persists).

- Tasks affected (failing, same mechanism — exact-command thrash):
  task_000408_0c17d946 (debugfs x17, budget_exceeded),
  task_001716_c1f2ac56 (python inline x22, budget_exceeded),
  task_000010_644ab1c2 (sleep;ps poll x10, done-fail 76 steps),
  task_000015_89886d8d (pytest x10, done-fail 54 steps),
  task_000118_3043e92d (cleanup x15, done-fail 74 steps),
  task_002160_7b255cce (x5, budget_exceeded),
  task_000020_eb7b8782 (x4, budget_exceeded).
- Signal: `exit_reason=budget_exceeded` on 6 fails; `max_exact_repeat >= 5`
  on the tasks above; the extreme cases (17, 22, 15, 10) consumed the whole
  80-step budget with the same command string.
- Verified (Read, body-quoted):
  - task_000408 step-seq: `debugfs /home/user/drive.img 2>&1 << 'EOF'\nls -l /\nEOF`
    executed **17 times** (Counter over full command strings), interleaved
    with `debugfs -R "cat 28"` x4 — never recovered, hit budget at step 80.
  - task_001716 steps 0–21: the *same* `python3 -c "...struct...PIL.Image..."`
    inline script re-run 22 times before the run died at step 80 with no
    `repo.pack` produced.
  - task_000010 top repeat: `sleep 10\nps aux | grep -E "mock_api|socat|python"`
    x10 — polling a process that never came up; ended done-fail, deliverable
    `/home/user/operator.py` missing.
  - task_000015 top repeat: `cd /home/user && python3 -m pytest test_parser.py`
    x10 — re-running the same failing test without changing the fix.
- Why Control not Instruction: the model *already* receives the self-verify
  and time/step-deadline nudges (prompt-level) and still thrashes — a prompt
  rule ("don't repeat commands") is a static instruction the model demonstrably
  ignores mid-loop. A Control hook fires *at the exact moment* the loop is
  detected, injecting the signal into the tool-result context the model is
  actively reading, which is where the intervention has to land.
- Why Control not Configuration: `CustomEditToolProcessor.threshold` only
  governs file-WRITE detection; the thrash commands here (debugfs/pytest/ps)
  write nothing, so no existing knob covers them — a new hook is required, not
  a retune.
- Retroactive check (A-corrective): yes — for task_408/task_1716/task_010 the
  loop *is* the blocker (the same command produces the same output for 10–22
  consecutive steps and consumes the budget). Firing at repeat #5 returns
  ~11–17 steps of budget and an explicit redirect signal at the moment the
  model is stuck, giving it room to try the different approach it never reached.
  Genuine correctness gaps unrelated to looping are untouched (they never
  cross the threshold).

- expected_global_gain: targets the 6 `budget_exceeded` fails + long
  exact-repeat done-fails (7 tasks) by returning wasted step budget and
  redirecting mid-loop. Even a 2–3 task flip is net positive at 30% baseline.
- regression_risk: LOW and bounded. The guard only *appends* text to a tool
  result — it never drops/rewrites messages (contract-clean) and never blocks
  the command. Passing runs with high repeats (task_123 echo x16, task_358 x4,
  task_1035 x5) either stay below threshold (x4) or receive a harmless extra
  nudge that cannot un-do already-correct work; an extra user-visible line does
  not change files already written. Rollback trigger: if R1 pass-rate drops
  below R0 (15) OR any currently-passing high-repeat task (123/358/1035) flips
  to fail, revert this processor.
- cost_shift: negligible-to-slightly-negative. Each fire adds ~90 tokens once
  per looping command; by breaking 10–22-step loops early it should *reduce*
  total tokens/steps on the pathological runs.
