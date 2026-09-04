# Candidates — R1/c0

Assigned focus: `task_000010_644ab1c2` (system_administration) fails.

## Diagnosis of task_000010_644ab1c2

Task: write `/home/user/operator.py` that backs up manifests, sets up a
9090→8080 port forward, and drives an interactive CLI (`pexpect`) to apply
manifests. `reward=0`, `exit_reason=done`, `finished=no_tool_calls`, 539.8s.

Two distinct signals in the trajectory, only one of which is a harness gap:

1. **Task-design trap (root cause of reward=0, NOT a clean harness fix).**
   The task *requires* the file at `/home/user/operator.py`. That filename
   shadows Python's stdlib `operator` module. When the verifier runs pytest
   from `/home/user` (cwd on `sys.path[0]`), `collections/__init__.py` does
   `from operator import eq` and picks up the user's file → circular import →
   pytest cannot even collect. `final_pytest` fails with
   `ImportError: cannot import name 'namedtuple' ... circular import`. The
   agent's script is otherwise *functionally correct* (backup created,
   socat 9090→8080 up, both manifests applied, `api_success.log` written —
   verified at steps 72-76). The agent even diagnosed the shadowing at step
   21 but the task forbids renaming. A harness fix here would require
   task-specific knowledge (rename/guard `operator.py`) → embeds a task
   answer, fails the generalization test. **Logged as a task/capability
   trap, not patched.**

2. **Length-truncation loop containment failure (the harness gap I ship).**
   Raw event log: **13 consecutive** `stop_reason=length` no-tool-call turns
   (steps ~39-69), each a full 4096-token generation of re-narrated
   analysis-paralysis about the operator.py conflict. That is ~1/3 of the
   run's effective budget spent looping with zero forward action.

   The pipeline already has `LengthTruncationRecoveryProcessor`
   (repeat_threshold=2) which collapses the runaway content and replaces the
   run-loop's passive "please continue" nudge with a corrective "issue ONE
   Bash command" instruction. Verified it *fires* (persisted assistant msgs
   are collapsed to ~1964 chars = head 1200 + marker + tail 600). But it
   **only ever nudges** — it has no upper bound. The model ignored the
   corrective nudge all 13 turns. Nothing terminated the loop:
   `CyclicLoopBreaker` keys on repeating tool-call cycles (a no-tool-call
   loop never enters its window); `ParseRetryProcessor` counts parse errors.

---

## Candidate C-001 — hard-stop escalation for the length-truncation loop

- **lens / lever / intent**: run-loop control-flow / Processor (evolve
  existing) / add-escalation-to-contain-degenerate-loop.
- **Signal**: `task_000010_644ab1c2` raw event log —
  `grep 'stop_reason":"length"'` = 13 consecutive no-tool-call truncations,
  steps ~39-69; corrective nudge fired every turn and was ignored every turn;
  run ended `no_tool_calls` after burning the turns.
- **Change**: author `processors/length_recovery_escalating.py` —a drop-in
  superset of `recipe.tmax_eval.processors.length_recovery.LengthTruncationRecoveryProcessor`.
  Keeps collapse + escalating-nudge behaviour verbatim; adds one mechanism:
  after `hard_stop_threshold` (default 6) *consecutive* length-truncation
  no-tool-call turns, raise `LoopDetectedError` from `on_after_model`. The
  run loop catches it → `exit_reason='loop_detected'` (clean, not `error`),
  `_recover_best_output` runs, container filesystem state is preserved for
  the verifier. Counter resets on ANY tool call or non-length finish, so a
  lone stray truncation followed by progress never trips it. `hard_stop_threshold<=0`
  restores the stock nudge-forever behaviour.
- **Config**: swap the stock `_target_` for the new `file://` path; add
  `hard_stop_threshold: 6`. Threshold 6 sits well above `repeat_threshold=2`
  so the escalating nudge gets 4+ chances before termination.

- **Retroactive check (counterfactual)**: on `task_000010`, the hard stop
  fires at truncation #6 instead of #13 — reclaiming ~7 full-length turns
  (~28k output tokens + wall-clock) and freeing that shared budget for other
  tasks in the round. It does NOT flip this task (the operator.py trap still
  breaks the verifier), but it prevents the loop from silently eating budget.
  Retroactive check on already-passing tasks: any task that never emits a
  no-tool-call length truncation is byte-for-byte unaffected (counter stays
  0, hard stop never arms). A task with a single incidental truncation then
  normal progress: counter resets on the next tool call → unaffected.

- **Why Processor-evolve not X**:
  - *Not system-prompt*: "don't loop" guidance is exactly what the corrective
    nudge already says and the model already ignores; more prose can't
    enforce termination. Control-flow needs a control-flow mechanism.
  - *Not a new separate processor*: the consecutive-truncation counter and
    the nudge state already live in `LengthTruncationRecoveryProcessor`;
    bolting a second processor on the same signal would duplicate state and
    risk ordering races. Evolving the one processor that owns the signal is
    the minimal, coherent edit.
  - *Not lowering `max_tokens`*: out of scope (set by harness_runner, not
    this YAML) and would harm legitimately long generations.

- **expected_global_gain**: reclaims budget on the class of
  "model freezes into consecutive full-length no-tool-call turns" runs
  (analysis-paralysis / repetition loops with no tool call). On step- or
  wall-clock-bounded batches this returns budget to other tasks; on runs
  where the freeze happens mid-task it stops burning turns that cannot
  improve the frozen workspace. Structural trigger → generalizes across
  domains, no task constants.
- **regression_risk**: LOW. The only behavioural change vs. stock is *early
  termination* of a run already in a 6-deep no-tool-call length loop — a
  state from which the stock processor's own evidence shows the model does
  not recover. Risk edge case: a model that would have recovered on turn 7+.
  Mitigated by threshold=6 (4+ nudge attempts first) and by the fact that a
  6-deep identical-failure-mode loop recovering is not observed in the
  evolve set. Termination is `loop_detected`, never `error`, so it does not
  trip the post-flight replay error gate.
- **cost_shift**: NET DOWN. Caps the worst-case per-task output-token spend
  on frozen runs (was unbounded up to the full step/wall budget; now ~6
  truncations max). No added cost on healthy runs (hooks are O(1), no extra
  model calls).
- **rollback trigger**: if the next round shows a task that previously
  passed now ending `loop_detected` at exactly the hard-stop boundary
  (i.e. the model was mid-recovery), raise `hard_stop_threshold` or set it
  to 0 to restore nudge-forever.
