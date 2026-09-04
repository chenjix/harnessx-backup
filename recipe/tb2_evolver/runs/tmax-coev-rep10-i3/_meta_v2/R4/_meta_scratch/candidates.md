# Candidates — R4

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `VerifierCliPrimerProcessor` that injects one idempotent, time-boxed,
non-fatal apt install of the standard networking CLI tools (`curl`, `ss` via
`iproute2`) on the first model turn, so a post-agent verifier that shells out
to these binaries does not die with `FileNotFoundError` before it can assert
on correct work.

- Tasks affected: task_001046_eccbf294, task_000300_7d6b511c
- Signal: `final_pytest.output_tail` on both failing tasks ends with
  `subprocess.py:1863: FileNotFoundError`. The literal missing binaries are
  named in the raised exception:
  - task_001046: `FileNotFoundError: [Errno 2] No such file or directory: 'curl'`
    on `test_end_to_end_pipeline` — the ONLY failing test (`1 failed, 4 passed`).
  - task_000300: `FileNotFoundError: [Errno 2] No such file or directory: 'ss'`
    on `test_haproxy_running_and_listening`.
- Verified (Read):
  - task_001046 messages.json — agent's own Bash call returned
    `bash: line 1: curl: command not found (exit 127)`, and the assistant
    reasoned "curl is not available. Let me try with wget or python." → the
    binary is genuinely absent in the container in BOTH phases; the agent
    worked around it, but the verifier cannot.
  - task_001046 result.json `final_pytest.output_tail` — the full CPython
    `subprocess.py` traceback terminating in
    `raise child_exception_type(errno_num, err_msg, err_filename)` /
    `FileNotFoundError: ... 'curl'`, i.e. the verifier itself invoked
    `subprocess.run(["curl", ...])`.
  - task_000300 result.json `final_pytest.output_tail` — identical
    `subprocess.py:1863` traceback ending in
    `FileNotFoundError: ... 'ss'`.
- Why Control not Action: the TB2 playbook states the agent has exactly one
  tool (`Bash`) and the tool registry cannot be extended — a new `@tool` is
  structurally impossible here. The gap is not the agent's action space but a
  missing *environment precondition* the verifier depends on and the agent has
  no reason to satisfy. A cross-task `MultiHookProcessor` that fires uniformly
  on turn 0 of every task is the right shape (same mechanism as the accepted
  R3 `requests` pip primer, one layer down: OS CLI instead of Python import).
  Not Instruction: telling the agent "install curl" in the prompt cannot help —
  the agent observed curl works around fine with wget/python and correctly
  concluded it does not need curl for ITS phase; only the hidden verifier does,
  and the agent never sees the verifier. A deterministic harness hook is the
  only place this knowledge can live.
- Retroactive check (A-corrective): partial-yes.
  - task_001046: yes — it is a clean single-test failure caused solely by the
    missing `curl` binary (`1 failed, 4 passed`); if `curl` were on PATH the
    verifier's `subprocess.run(["curl", ...])` resolves and the test asserts on
    the agent's pipeline, which produced the 4 already-passing checks. High
    confidence flip.
  - task_000300: this fix removes ONE of three failing tests
    (`test_haproxy_running_and_listening` FileNotFoundError on `ss`); the other
    two (`test_test_results_log` AssertionError, `test_stream_analyzer_backends_running`)
    are independent and likely capability gaps. So `ss` is necessary-not-
    sufficient for this task — the fix unblocks the structural test but the task
    may stay red. Documented; not counted as a confident flip.
- expected_global_gain: closes the "verifier shells out to a CLI tool absent
  from the base image → FileNotFoundError → automatic 0" failure class. This is
  the exact structural sibling of the R3 pip primer that was ACCEPTED and
  flipped 3 tasks; the class recurs across ≥2 tasks with different tools
  (`curl`, `ss`) and different domains (data_processing, software_engineering),
  so it generalizes beyond the two cited tasks to any future service/networking
  task whose verifier probes with a standard CLI.
- regression_risk: Low. The install is (1) guarded by `command -v curl && command -v ss`
  so it is a fast no-op on every task where the tools already exist (which is
  most — only the two cited tasks show the absence); (2) time-boxed with
  `timeout 90` so a blocked/slow package index cannot stall the run;
  (3) terminated with `|| true` and appended to the model's own turn-0 call, so
  it can never fail the turn or suppress the model's first action; (4) writes
  only to system package state / `/usr/bin`, never a task output path, so it
  cannot corrupt a correct solution. No currently-passing task uses these
  binaries in a way this could change (a passer that used curl/ss would already
  have them present → no-op). Rollback trigger below covers the residual risk.
- cost_shift: +1 tool call on turn 0 of all 50 tasks. On the ~48 tasks where
  the tools are already present it is a sub-second `command -v` no-op. On the
  two tasks where install actually runs it costs one bounded apt fetch
  (≤90s hard cap). Negligible token cost (output suppressed to /dev/null).

- Rollback trigger: revert to R3 if global pass-rate drops below R3 (13/50),
  OR if the synthetic replay errors on the injected turn-0 tool call, OR if any
  currently-passing task flips T→F.
