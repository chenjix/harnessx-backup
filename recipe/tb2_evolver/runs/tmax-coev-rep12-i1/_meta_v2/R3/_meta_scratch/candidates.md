# Candidates — R3 (tmax-coev-rep12-i1)

Round-2 trajectories are R0 config re-measured (R1 was reverted). Score
25/50 this draw; R0 repeats were 29,26 (high variance). Failing cluster
sweep over `result.json` (`final_pytest.output_tail`) surfaced one clean,
structural, verified cluster below.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add an exit-time `VerifierDepGuardProcessor` that ensures common verifier
test-dependencies (`requests`, `pyyaml`) are importable in the container
Python before the agent finishes, via one guarded, idempotent `pip install`
Bash call injected on exit-intent.

- Tasks affected: task_002108_a8cfbf2a (file_operations), task_001857_24daeef3
  (debugging), and partially task_000796_828a72cf (security).
- Signal: `final_pytest.output_tail` on all three shows an identical
  pytest **collection** abort:
  `ImportError while importing test module '/tmp/test_final_state.py'` →
  `ModuleNotFoundError: No module named 'requests'` →
  `Interrupted: 1 error during collection`. The verifier probes a running
  HTTP server with `requests`, which is absent from these base images.
- Verified (Read of `.messages.json`):
  - task_002108: agent built a Rust HTTP frame server, tested it itself with
    stdlib `urllib.request` — tool output shows `Status: 200`,
    `Content-Type: image/jpeg`, wrong/absent token → `401 Unauthorized`, and
    a final `ps` showing the server live (`./target/release/frame_server`,
    PID 1293, not defunct). Solution is correct; only the verifier's
    `import requests` failed.
  - task_001857: agent fixed the C++ off-by-one, ran the diagnostic server
    (`./diagnostic_server` live in `ps`), and its own checks returned
    `{"status": "healthy"}` on the HTTP port and `FRAMES: 450` on the TCP
    port. Solution is correct; only the verifier's `import requests` failed.
  - task_000796: server still crashing at budget exhaustion (partial), so
    the guard alone may not flip it, but it removes the collection-abort
    masking the real test result.
  - pip availability confirmed in-env: task_001968_3adc0f9b tool output shows
    `Successfully installed ... certifi-2026.7.22 charset_normalizer-3.5.1
    idna-3.19 ...` — i.e. requests' own dependency chain downloaded and
    installed successfully during a normal task, so `pip install requests`
    works here.
- Why Control not Instruction: the gap is not knowledge the agent could act
  on — the task description never mentions `requests`; it is a hidden property
  of the post-exit verifier. A prompt rule ("install requests") would be a
  task-specific literal that fails the generality test and could not fire
  reliably. A mechanical `on_after_model` hook that injects a real Bash call
  is the only shape that guarantees the install actually runs before exit,
  uniformly across every task, regardless of what the agent believes.
- Why Control not Action: no new agent capability is needed — the agent
  already has Bash; the missing piece is that the install must fire
  deterministically at session end, which is a loop-level guard, not an
  action the agent chooses.
- Retroactive check (A-corrective): yes — for task_002108 and task_001857 the
  agent's solution is verified-working via its own probes; the ONLY failure is
  `import requests` at verifier collection. Had `requests` been importable, the
  test module would collect and the passing assertions would score the task.
- expected_global_gain: flips the "verifier imports a common test lib absent
  from the image" class (≥2 verified clean flips this round: 002108, 001857).
  Generalizes to any future HTTP-service task whose verifier probes with
  `requests` — a recurring TB2 shape.
- regression_risk: near-zero. Installing an already-present module is a no-op
  (guarded by `python3 -c 'import mod'` first); every branch ends in `|| true`
  so the injected Bash call can never fail the run; fires at most once per
  task and only on exit-intent, and coordinates with the order-90 self-verify
  processor (stays silent when self-verify's keepalive already occupies the
  exit turn). No currently-passing task hit this error, so none can regress.
- cost_shift: +1 short Bash round-trip on runs that reach exit-intent (a few
  hundred tokens for the dep-check output + one ack message). Negligible;
  no effect on the many tasks that already have the deps (import short-circuits).
