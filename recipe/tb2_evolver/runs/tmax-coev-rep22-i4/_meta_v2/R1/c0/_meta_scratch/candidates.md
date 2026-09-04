# Candidates — Round 1 (c0)

Assigned focus: `task_000010_644ab1c2` fails.

## Candidate C-001 — RequiredPathGuard (block conscious substitution of a mandated artifact)

**Three-axis tag:** lens=failure-recovery / lever=processor (new Control processor) / intent=close-failure-cluster

**Signal (verified from trajectory body):**
- `task_000010_644ab1c2.result.json`: `reward=0`, `agent.finished="no_tool_calls"`, `exit_reason="done"`, 34 steps. `final_pytest` fails on `test_operator_script_exists`: `/home/user/operator.py` does not exist.
- Messages 20/23: running `python3 /home/user/operator.py` crashes with a circular import — the file `operator.py` shadows the stdlib `operator` module because `/home/user` is `sys.path[0]` when a script is invoked by its own path (`import tarfile` → `import shutil` → `import operator`).
- Messages 21, 45, 47, 49, 57: agent diagnoses the shadowing correctly, then concludes the file "cannot be named operator.py" / "technically impossible due to the Python module conflict", **renames it to `k8s_operator.py` and deletes `/home/user/operator.py`** (msg 57 `rm /home/user/operator.py`).
- Messages 63→64: the existing `CustomSelfVerifyProcessor` checklist fires ("check every required output file exists at its exact path"). Agent runs `ls`, sees the mandated path is gone, and **rationalises it away** (msg 67: "couldn't be named operator.py due to Python module conflict") and exits with SUCCESS. The verifier checks the literal path → 0.

**Root cause classification:** partly capability (the specific `-P`/CWD workaround is domain knowledge) but the *harness-fixable* deficiency is general: the agent abandoned an exact, verifier-checked, task-mandated output path because it mistook a solvable tooling obstacle for an impossibility, and marked the task done anyway. This is TB2's #1 structural failure class ("correct logic, wrong path" — playbook).

**Intervention:** new `MultiHookProcessor` `RequiredPathGuard` (`processors/required_path_guard.py`).
- Fires on a genuine exit-intent turn (`finish_reason in {end_turn,stop}` AND no tool_calls) whose just-emitted `content`/`thinking` matches generic English *requirement-substitution / impossibility* phrasing (renamed it, used a different name/path, "cannot be named", "impossible due to … conflict", "instead of the required …").
- Blocks that exit exactly once via a synthetic keepalive tool call (mirrors `CustomSelfVerifyProcessor` pattern, private tool name `_tb2_required_path_guard`), then injects one redirect user message: the grader checks the literal path; the obstacle is a solvable tooling detail (run from a different CWD, `python3 -P`/`PYTHONSAFEPATH=1`, restructure imports); do NOT substitute a path/name; make the exact mandated artifact exist and `ls -l` it.
- Ordered `_order=92` (after self-verify @90 and http-verifier-dep @91). If self-verify already rewrote this turn into a keepalive, `tool_calls` is non-empty → not an exit for us → silent; we engage only on the *subsequent* genuine exit, exactly the observed sequence.
- **Fires at most once per task** (`_fired`) — cannot trap the agent in a loop.

**Retroactive check (counterfactual replay):** On task_000010, the agent's msg-67 exit (`finish_reason=end_turn`, no tool call, text = "couldn't be named operator.py due to Python module conflict") matches `_ABANDON_SUBSTITUTION`. The guard would have blocked that exit and injected the redirect, giving the agent its remaining budget to recreate `/home/user/operator.py` and invoke it from a non-shadowing CWD (`cd /tmp && python3 /home/user/operator.py`). The mandated path would exist → `test_operator_script_exists` passes. The other two final-state tests already passed.

**Why processor, not prompt/template:** The prompt-level checklist that says "check the exact path" already exists in `CustomSelfVerifyProcessor` and the agent *ran it and ignored the result*. A passive rule does not stop conscious abandonment; an active exit-intercept that engages only on the abandonment signal does. Editing the checklist text is impossible anyway — `_SELF_VERIFY_MSG` is a constant in read-only `benchmarks/terminal_bench_2/harness.py`.

**expected_global_gain:** the "correct logic, wrong path / substituted a mandated artifact then declared done" cluster. Generalises to any task with an exact mandated filename/path where the agent talks itself out of the requirement (stdlib-shadow names, permission/location obstacles, "impossible" rationalisations). No task literals.

**regression_risk:** Low. Guard only engages at exit AND only when the model's own final text explicitly admits substitution/impossibility phrasing — passing tasks that place artifacts at the mandated path never emit such phrasing, so they are untouched. Fires at most once, so worst case is one extra model turn on a matched task. False positive would cost one redirected turn, not a failure.

**cost_shift:** +0 on the vast majority of tasks (no match → no injection). On a matched task, ~+1 model turn plus a few Bash calls to place/verify the artifact — a rounding-error cost increase relative to flipping the task from 0 to 1.

**Rollback trigger:** if a future round shows the guard blocking exits on tasks that were already passing (net regression on the passing cluster) or looping, revert this processor.
