# Candidates — R3 c2

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace `CustomSelfVerifyProcessor` with a drop-in `HttpVerifierDepGuardProcessor`
that adds one conditional exit-checklist item: on runs where an HTTP-service
signal was observed, remind the agent to ensure the Python `requests` library is
importable (installing it if missing) so the external HTTP verifier can collect
and run its tests.

- Tasks affected: task_000028_7fe033ac, task_000958_4bb2b05d
- Signal: `final_pytest.output_tail` on both tasks =
  `ImportError while importing test module '/tmp/test_final_state.py' ...
  import requests → ModuleNotFoundError: No module named 'requests'`; pytest rc=2,
  "Interrupted: 1 error during collection". Both are HTTP-service deliverables
  (nginx reverse proxy + C++ socket server on 127.0.0.1:8080; C++ httplib service
  on 127.0.0.1:9090). Verifier never even runs an assertion — collection fails.
- Verified (Read):
  - task_000028 messages.json: agent fully solved the task — msg (final HTTP test)
    `Status: 200 / Body: '150'` through nginx→unix-socket→C++ server; fixed the
    502 permission-denied; logrotate.conf correct; `exit_reason=done`,
    `finished=no_tool_calls`, reward=0 SOLELY because the verifier's
    `import requests` failed at collection. The agent itself discovered the box
    had NO http clients (`curl: command not found`, `wget failed`, `nc` absent)
    and fell back to `python3 urllib` — a direct sign the environment lacked HTTP
    tooling the verifier depends on, and the agent never provisioned `requests`.
  - task_000958 result.json: identical `import requests` collection ImportError
    (line 4 of the same test module), reward=0.
  - Cross-task evidence that install is viable: task_000684 (`Downloading
    numpy ... scipy ... Successfully installed`), task_001818 (torch/whisper from
    PyPI), task_001652 (pytesseract) — outbound `pip install` succeeds in these
    containers, so `pip install requests` closes the gap.
- Why Control not Instruction: the fix is a mechanical, runtime-gated
  provisioning step, not a reasoning skill. An unconditional system-prompt rule
  would fire on the ~majority of non-service tasks (cost/noise) and cannot be
  gated on runtime evidence that the agent actually stood up an HTTP service.
  Reusing the existing exit-verify Control mechanism (keepalive + one user
  message) lets me condition the extra item on an observed HTTP signal
  (`on_before_tool` Bash-command regex), firing it only where it matters.
- Why Control not Configuration: `CustomSelfVerifyProcessor` exposes no knob for
  a conditional checklist item; the behavior change requires new hook logic.
- Retroactive check (A-corrective): yes — task_000028 was functionally complete;
  had `requests` been installed the verifier would have collected and passed. The
  nudge fires exactly at the voluntary-exit moment both tasks reached
  (`finished=no_tool_calls`), and the HTTP signal (nginx / 127.0.0.1:8080 /
  httplib / HTTP/1.1) was present in both runs' Bash commands.
- expected_global_gain: Flips the verifier-side `import requests` collection
  cluster (2 tasks here; generalizes to any HTTP-service task whose grader uses
  Python `requests`). Mechanism is class-wide, not task-specific.
- regression_risk: Low. Append-only, one-shot, byte-identical to the stock
  self-verify on non-HTTP runs (regex gate → extra item suppressed). Worst case:
  one extra `pip install requests` (~a few seconds) on an HTTP task that already
  had `requests`. No message removed, no process killed, no schema/knob changed.
  Contract check: 0 violations.
- cost_shift: Negligible. One conditional ~150-token checklist item + at most one
  short `pip install` on HTTP-service tasks; net-favorable since it converts
  guaranteed-0 verifier-collection failures into gradable runs.
- rollback_trigger: If R4 shows task_000028/task_000958 still failing with the
  same `ModuleNotFoundError: requests` collection error (nudge not acted on), OR
  any previously-passing HTTP-service task regresses, revert to stock
  `CustomSelfVerifyProcessor`.
