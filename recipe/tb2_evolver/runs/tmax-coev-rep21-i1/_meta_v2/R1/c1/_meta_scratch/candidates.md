# Candidates — Round 1 (c1)

Assigned focus: `task_000015_89886d8d` fails (`budget_exceeded`, reward 0).

## Diagnosis of task_000015_89886d8d

The task requires OCR-ing a schema image (`tesseract`), then writing
`/home/user/migrate.py`, an output `.jsonl`, and a hypothesis test
`/home/user/test_parser.py`. The agent burned all 80 steps in two
byte-identical repetition loops and never wrote `test_parser.py`:

- Steps 2/4/6: the SAME failing tesseract command (md5 `af20d2d4`) ×3.
- Steps 27/35/37/39/41: the SAME `cat > /home/user/migrate.py << EOF`
  (md5 `8b2170e5`) ×5; steps 45/49 the SAME rewrite (`cb24d40d`) ×2.

The model's own assistant text confesses the loop: "I keep writing the
same code" (steps 39,41,43,47). The underlying logic bug (path param
extracted as `p0`, mapping expects `item_id`) is a model capability gap
— but the *runaway identical-command repetition that consumed the whole
budget* is a harness deficiency: nothing in the pipeline detects or
breaks "same Bash command → same output, N times in a row."

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add `StallLoopBreakerProcessor`: track normalized Bash command
signatures in a sliding window; on the 2nd recent identical repeat
append a corrective note to the tool result, and on the 3rd
short-circuit the call (`approved=False` + synthetic result) so the
identical command does not re-execute and burn budget.

- Tasks affected (failing, same mechanism — byte-identical command
  loops, verified by md5 of tool-call args):
  - task_000015_89886d8d — tesseract ×3, `cat>migrate.py` ×5
    (`budget_exceeded`, reward 0)
  - task_001706_24462a09 — `redis-cli LLEN csv_input` ×33 consecutive
    (`budget_exceeded`, reward 0)
  - task_000338_27d6a1be — `convert /app/kernel_spec.png ...` ×~20
    (`budget_exceeded`, reward 0)
  - task_000958_4bb2b05d — maxconsec 26 (`budget_exceeded`, reward 0)
  - task_001089_220cc46b — maxconsec 27 (`budget_exceeded`, reward 0)
  - task_001697_af4b85fb — maxconsec 27 (reward 0)
  - task_001536_acfe6c35 — maxconsec 22 (`budget_exceeded`, reward 0)
  - task_001031_a8f0eb37 — maxconsec 26 (`error`, reward 0)
  - (also: task_000010, task_001090, task_001321, task_001673,
    task_001937 — all maxconsec≥9 and reward 0)
- Signal: computed max consecutive identical tool-call-argument md5
  across all 50 trajectories. `maxconsec >= 8` occurs on ~15 tasks;
  14 of 15 have `reward == 0`. Tasks with `maxconsec == 1`
  overwhelmingly pass. Strong correlation between repetition loops and
  failure.
- Verified (Read of messages.json, md5 of tool_calls[].function.arguments):
  - task_000015: steps 2,4,6 identical hash `af20d2d4`; steps
    27,35,37,39,41 identical `8b2170e5`; assistant text at steps
    39/41/43/47 literally reads "I keep writing the same code."
  - task_001706: steps 2,4,6,9,11,13,...,64 all hash `c8ac2acd`
    = `redis-cli LLEN csv_input`, 33 identical calls in a row.
  - task_000338: steps 2,4,6,9,15,19,21,23,25 hash `f1abcf93`
    = `convert /app/kernel_spec.png ...`, repeated with no change.
- Why Control not Instruction: the model already *knows* it is looping
  (it narrates "I keep writing the same code") and still cannot stop —
  a prompt rule telling it "don't repeat commands" will not help a model
  that is already aware and stuck. The only reliable break is a
  mechanical interceptor that refuses to re-run the identical command
  and injects an escalating corrective signal. This must fire uniformly
  on every task, which a per-call tool cannot express.
- Why Control not a wider Configuration tweak: no existing knob covers
  "identical command repeat." `CustomEditToolProcessor.threshold` only
  counts same-file writes (misses read-only loops like `redis-cli`) and
  at 7 fires far too late (loops here run 20-33 times).
- Why Control not Action: the agent has the right action (`Bash`); the
  problem is repeating it uselessly, not lacking a capability.
- Retroactive check (A-corrective): yes — for the pure-repeat loops
  (task_001706 `redis-cli` ×33, task_000338 `convert` ×~20, task_000015
  tesseract ×3), blocking the identical re-run at repeat 3 reclaims
  ~20-30 wasted steps of budget and forces a different action well
  before `budget_exceeded`, giving the agent the room to actually reach
  the output-writing phase. For task_000015 specifically it also frees
  the budget the agent needed to write the missing `test_parser.py`.
- expected_global_gain: the largest failing cluster in the round —
  ~14 reward-0 tasks share the identical-command-loop mechanism. Even
  flipping a fraction is a big net gain; the mechanism is fully generic
  (hash of the command string), so it generalizes to unseen tasks.
- regression_risk: LOW. Only byte-identical (whitespace-normalized)
  commands repeated >=3 times in a 6-call window are ever affected.
  Passing tasks in this round have `maxconsec == 1` (no identical
  consecutive repeats), so none are touched. The one risk is a
  legitimate poll loop (e.g. "wait for a service, re-check every few
  seconds") — mitigated by (a) the window resets as soon as any
  different command runs, and (b) the block message explicitly tells
  the agent to vary the command (e.g. add `sleep`), which also breaks
  the identical signature. No passing task in the round exhibits a
  benign identical-command poll.
- cost_shift: strongly NEGATIVE (saves cost). Blocking 20-30 redundant
  identical Bash executions per stuck task cuts tokens and wall-clock;
  the injected notes are short (a few dozen tokens).
