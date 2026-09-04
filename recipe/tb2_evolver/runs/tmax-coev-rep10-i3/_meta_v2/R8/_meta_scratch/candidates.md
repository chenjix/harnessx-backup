# Candidates — R8

## Candidate C-001
[lens: failure | lever: instruction | intent: corrective]

Add a general **deliverable-fidelity** rule to the shared system prompt:
honor the exact output path/filename the task specifies verbatim — never
rename or relocate a required deliverable on your own judgement (e.g. to
dodge a stdlib name clash) — and before stopping, `ls` each explicitly
named output path to confirm it exists at that exact location.

- Tasks affected (acute, harness-addressable): task_000010_644ab1c2
  (primary clean flip). Supporting class evidence: the TB2 verifier
  opens with hard `os.path.isfile(<exact path>)` / executable-at-exact-path
  gates (task_000010 `test_operator_script_exists`, task_000212
  `test_keygen_executable_exists`) — a deviated deliverable path is an
  automatic 0 regardless of content correctness (playbook: "Correct
  logic, wrong path").
- Signal: `final_pytest.output_tail` on task_000010 =
  `AssertionError: Operator script /home/user/operator.py does not exist.
  You must create it.` with `1 failed, 2 passed` — the ONLY failing test
  is the exact-path existence gate. `exit_reason=done` (agent voluntarily
  stopped believing it was finished).
- Verified (Read, task_000010 messages.json, final turns):
  - Agent reasoning: "The task says the script should be at
    `/home/user/operator.py`, but I renamed it to
    `/home/user/k8s_operator.py` to avoid the conflict with Python's
    standard library `operator` module."
  - Its own probe returned: `operator.py not found at /home/user/operator.py
    -rwxr-xr-x ... /home/user/k8s_operator.py` — i.e. the agent SAW the
    deliverable was missing at the required path.
  - Final assistant message: "The task is complete. The script is at
    `/home/user/k8s_operator.py` (renamed from `operator.py` ...)." The
    functional work (backup, port-forward, manifest apply) was done and
    the API log confirms manifests applied — only the filename deviated.
  - Regression baseline: passing tasks already respect exact paths
    (task_000041 writes `/home/user/filter_billing.py` +
    `/home/user/billing_backup.tar.gz`; task_000577 writes
    `/home/user/recovered_evidence.txt`; task_000911 writes
    `/home/user/enforce_policy.py`) — the rule codifies a habit passers
    already have, so it cannot regress them.
- Why Instruction not Control: a processor cannot reliably know WHICH
  paths a post-agent-phase verifier will `isfile()`-check — those test
  files do not exist during the agent phase (playbook: verifier files
  injected after the agent exits). The failure is an awareness/judgement
  gap: the agent KNEW the required path (it re-read the task and probed
  the missing file) and consciously chose to deviate. That is exactly the
  Instruction lever — encode "the task's stated path is authoritative;
  resolve name clashes inside the module, not by renaming the file".
  A mechanical hook that renamed files would risk destructively clobbering
  correct work on the other 49 tasks.
- Why not Action: the agent already has full Bash to `mv`/create the file
  at the exact path; no new capability is missing.
- Retroactive check (A-corrective): yes — had the rule been present,
  task_000010's agent (which had a complete, working script and had
  already observed the exact-path file was missing) would have kept/created
  `/home/user/operator.py` at the required path. Its sole failing test was
  the exact-path existence gate; every other test passed. The stdlib
  `operator` clash it feared is resolvable without renaming the entrypoint
  (the module only needs the file to exist at that path; imports can be
  local/renamed internally).
- expected_global_gain: flips the "renamed/relocated deliverable → exact-
  path existence gate fails" class. TB2 verifiers consistently front-load
  `os.path.isfile(<exact path>)` gates; any unseen task that names an
  explicit output path benefits from the "path is authoritative + confirm
  before stop" discipline. Acute flip: task_000010.
- regression_risk: ~30 extra prompt tokens appended to the shared 5-line
  prompt across all 50 tasks. The rule only tells the agent to use the
  path the task itself specifies and to verify it — it introduces no
  task literals and cannot make a passer write to a wrong path (passers
  already honor exact paths). No currently-passing task relies on renaming
  or relocating a required deliverable.
- cost_shift: negligible (+~30 prompt tokens/task, no extra tool calls).
  May slightly REDUCE cost on affected tasks by turning a wasted verify
  loop into a clean pass.
