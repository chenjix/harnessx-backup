# Candidates — R2/c2

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Extend the one-shot self-verify exit gate with a conditional step for
HTTP-graded tasks that reminds the agent to make the Python `requests` HTTP
client importable before exiting, because the TB2 verifier's test module
imports it and it may be absent from the container.

- Tasks affected: task_000028_7fe033ac, task_000958_4bb2b05d
- Signal: both tasks have `reward=0` with `final_pytest.rc=2` and
  `output_tail` = `ImportError while importing test module ... /tmp/test_final_state.py
  ... import requests / ModuleNotFoundError: No module named 'requests'` —
  a pytest *collection* error, not an assertion failure. Both `initial_pytest`
  passed (rc=0), both `finished=no_tool_calls`, both build an HTTP service.
- Verified (Read):
  - task_000028 msg IDX 65: agent's own HTTP smoke test returns
    `HTTP/1.1 200 OK ... 150` — the nginx→C++ unix-socket service is fully
    correct. IDX 66 final message confirms all 5 objectives. Yet
    `final_pytest` = ModuleNotFoundError('requests') at collection. Agent never
    ran `pip install requests` (grep of full messages: `install requests`
    absent).
  - task_000958 (data_querying): builds a C++ HTTP server on 127.0.0.1:9090;
    `final_pytest.output_tail` = same `import requests` collection error at
    `/tmp/test_final_state.py:4`. Agent never installed requests.
  - Cross-check: task_001498_df8254c9 (data_science, HTTP verifier) passed —
    its container already had `requests`. So availability is inconsistent
    across task images; the guard only matters where it's absent.
- Why Control not Instruction (system prompt): this is a *verifier-environment*
  mechanical fact, not general task strategy, and it must fire at the exit
  boundary only for the HTTP-graded subset (conditioned on task text). Putting
  it in the always-on system prompt would inject verifier-install noise into
  every non-HTTP task; the exit-gate hook already exists (behavioral
  self-verify) and is the natural, scoped place. Not Action: the agent already
  has Bash and can install packages — the gap is that it does not *know* the
  hidden grader needs `requests`, not that it *cannot* install it.
- Why Control not a proactive auto-install processor: a processor cannot run
  Bash in the sandbox; the install must be executed by the agent. The hook can
  only inject the nudge, and the one-shot exit gate is exactly that mechanism.
- Retroactive check (A-corrective): yes — if the checklist step had been in
  context at the exit turn of task_000028 / task_000958, the agent would have
  run `python3 -c "import requests"`, seen it fail, and installed it (best
  effort via pip/apt), letting the grader collect and run against the
  already-correct service. The blocker is purely the missing grader dep, which
  this nudge targets directly.

### Pareto framing
- expected_global_gain: closes the "correct HTTP service, verifier can't
  `import requests` at collection → score 0" cluster (>=2 tasks here across two
  domains: system_administration, data_querying). Generalizes to any TB2 task
  whose grader test module imports `requests`, which is the common HTTP test
  client on this benchmark.
- regression_risk: Low. The behavioral checklist is preserved byte-for-byte
  (HttpVerifierDepGuard extends BehavioralSelfVerifyProcessor); the extra step
  fires ONLY when the task description matches an HTTP-verifier signal
  (verifier/grader + http/endpoint/port/curl). Non-HTTP tasks see the identical
  prior checklist. Worst case on a matched task where `requests` already exists:
  one cheap `python3 -c "import requests"` check that succeeds and a no-op. The
  install is best-effort — if internet/pip/apt are blocked it fails silently and
  the task is no worse off than today.
- cost_shift: negligible; +~1 short Bash check (and possibly one install) on the
  HTTP-verifier subset only, at the single exit turn. No per-step overhead.

### Rollback trigger
If R3 shows pass_rate flat/down AND new T->F regressions on HTTP tasks that
previously passed (e.g. the install step wedges a service or eats the time
budget), revert to plain BehavioralSelfVerifyProcessor.
