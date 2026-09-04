# Candidates — R2 c0

Assigned focus: `task_000010_644ab1c2` (system_administration, reward=0).

## Diagnosis (assigned task)

Under R1's config the run is short now (116s, 30 steps, `finished=no_tool_calls`)
— the length-loop hard-stop change worked, so the freeze is gone. The *current*
failure is different and cleanly generalizable:

- The task **explicitly** requires the deliverable at `/home/user/operator.py`.
- The agent wrote it there, ran `python3 /home/user/operator.py`, and hit a
  circular-import error because `operator.py` shadows Python's stdlib `operator`
  when the script's own dir (`/home/user`) is `sys.path[0]`.
- The agent "fixed" it by **renaming the deliverable to `/home/user/k8s_operator.py`**.
  The script then ran and produced correct backups/applies — but the verifier
  checks `os.path.isfile('/home/user/operator.py')` → **file does not exist → fail.**
- The self-verify checklist DID fire (`_tb2_self_verify` at step ~19). On re-read
  the agent explicitly noticed "the task says `/home/user/operator.py` but I
  renamed it" — yet still kept the wrong name, believing the exact path was
  impossible to satisfy. It then hit the token limit and gave up.

Root cause is an **instruction gap**, not a capability gap: the agent had the
means to keep the file at the required path (run it from a different cwd, or run
a differently-named execution copy while leaving the canonical deliverable in
place) but did not know the *principle* that a required exact output path is a
hard, non-negotiable constraint that survives any workaround.

This is the top structural failure shape on TB2 ("Correct logic, wrong path" —
playbook). Across this round's 50 tasks, **16 failing tasks** trip a verifier
`os.path.isfile(<exact_path>)` / `os.path.exists(<exact_path>)` assertion
(task_000010, _000111, _000118, _000264, _000321, _000396, _000635, _000760,
_000933, _001048, _001116, _001492, _001547, _001704, _001877, _001937). Not all
share the *rename-to-dodge-an-error* mechanism, but all share the exit-time
symptom the self-verify checklist is supposed to catch and currently under-serves:
the deliverable did not land at the required exact path.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Enrich the (already-active) self-verify exit checklist with a **deliverable
path-integrity** step: at exit, reconcile every required exact path from the task
description against what actually exists on disk, and treat a *deliberate*
rename/relocation of a required deliverable (done to dodge a runtime error) as a
failure to be undone — keep the canonical file at the required path and adapt the
workaround (execute from a different cwd / under a different execution-only name)
instead.

- Tasks affected (same exit-time mechanism — deliverable absent at required
  exact path): task_000010_644ab1c2 (renamed operator.py→k8s_operator.py to dodge
  stdlib shadow), and the broader missing-exact-path cluster it shares a checklist
  with: task_000118_3043e92d, task_000264_ab8c7253, task_001547_8cde5da2,
  task_001877_bd2513aa (verifier fails on `isfile(<exact_path>)`).
- Signal: `final_pytest.output_tail` contains
  `assert os.path.isfile(<path>) ... does not exist` on 16/50 tasks;
  for task_000010 the agent's own step-19 self-verify re-read explicitly says
  "task says /home/user/operator.py ... I renamed it to k8s_operator.py".
- Verified (Read of task_000010_644ab1c2.messages.json):
  - step ~13 assistant: created `/home/user/operator.py` via heredoc.
  - step ~15 tool: `python3 /home/user/operator.py` → `ImportError: cannot import
    name 'deque' from partially initialized module 'collections' ... circular
    import (/usr/lib/python3.10/...)` (the operator.py shadow).
  - step ~16 assistant: "There's a naming conflict ... I need to rename the
    script" → `mv /home/user/operator.py /home/user/k8s_operator.py`.
  - step ~19 `_tb2_self_verify` fires; assistant re-read: "The task explicitly
    says to create the script at /home/user/operator.py ... I renamed it to
    k8s_operator.py". Still keeps the renamed file; then hits the token limit.
  - `final_pytest`: `AssertionError: Operator script /home/user/operator.py does
    not exist.`
- Why Control not Instruction: the guidance rides on the **existing exit-
  interception mechanism** (`CustomSelfVerifyProcessor` / `NumericSelfVerifyProcessor`
  fire-once keepalive + injected user message). The whole point is that the
  reconciliation must fire *at the exit boundary*, after the agent believes it is
  done — a static system-prompt rule read at step 0 is exactly what the agent
  already had (it re-read the constraint and ignored it). Delivering the principle
  at the decisive step, inside the checklist the agent is forced to answer before
  exiting, is a Control-hook property, not a prompt-text property. The new class
  shares `_singleton_group="tb2_self_verify"` so it REPLACES the active
  NumericSelfVerifyProcessor (only one self-verify fires) and preserves the
  numeric cross-check step verbatim.
- Why not just tune Configuration: there is no knob on the self-verify processor
  for the checklist text; the checklist body is the lever.
- Retroactive check (A-corrective): yes — for task_000010, a checklist step that
  says "a required file you renamed to dodge an error must be restored to its
  exact path; adapt the workaround, not the deliverable" hands the agent both the
  verdict (this is a failure) and the remedy (keep operator.py, run it from
  elsewhere / copy for execution). The agent had already surfaced the deviation on
  its own; it only lacked the principle that the path is non-negotiable.
- expected_global_gain: the missing-exact-path cluster is the single largest
  failing shape this round (16/50). The step generalizes because it is phrased as
  a path-reconciliation strategy with zero task literals — it helps any task whose
  deliverable drifted from the required path (rename, wrong dir, wrong extension).
- regression_risk: low. The step only adds text to a checklist that already fires
  exactly once per task and never blocks exit; a correctly-placed deliverable
  passes the step trivially. No new termination path, no new tool. Worst case is a
  handful of extra `ls`/`mv` Bash calls on tasks that were already going to pass.
- cost_shift: near-zero. One-shot injected message, slightly longer than the
  current checklist (~10 extra lines). No added turns on healthy runs; may add 1-2
  corrective Bash calls on tasks that had drifted paths — cheaper than the failed
  round it prevents.
- rollback_trigger: next round shows a previously-passing task newly failing with
  the agent thrashing on path reconciliation, or the self-verify message inflating
  step counts materially on the passing cluster → revert to NumericSelfVerifyProcessor.
