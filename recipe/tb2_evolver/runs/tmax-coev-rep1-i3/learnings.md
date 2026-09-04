# Evolve journal — tmax-coev-rep1-i3

## Round 1 — lifecycle ground-truth snapshot

<!-- journal:frontmatter
round: 1
timestamp: 2025-08-17T09:00:00Z
hypothesis_id: h_lifecycle_snapshot_v1
levers: [control]
predicted_affected: [task_000140_01c78b42, task_001090_c61c71f2]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=29/50; +4/-2 gained=task_000329_a3ac56b0,task_000396_e56917e2,task_000587_9862bb19,task_001090_c61c71f2 lost=task_001031_a8f0eb37,task_001818_b251e5ea; score 0.5800 >= incumbent(mean) 0.5400 - tol 0.0400
expected_global_gain: "Flips the process-lifecycle sub-cluster in system_administration (0/5); generalizes to any background-service/PID-recording task"
regression_risk: "One extra Bash round-trip + one checklist turn on every run reaching exit intent; negligible behavior change on pure-logic tasks"
cost_shift: "+1 Bash tool call and ~1 short model turn per run at exit intent; snapshot output bounded"
rollback_trigger: "If R2 pass_rate is flat/down vs R0 27/50 and system_administration is still 0/5, or if the injected Bash snapshot causes any exit_reason=error in replay/eval, revert"
-->

### Why

R0 scored 27/50 (54%). system_administration was the worst cluster at 0/5.
Two of those failures share one mechanism: the agent leaves background
service processes lingering / mis-records a PID, and the verifier fails on
lifecycle assertions even though the task's logic is correct.
task_000140 failed only `test_no_lingering_service_processes` (lingering
`vm_service` PIDs); task_001090 failed only `test_pid_file_and_process`
(`pid.txt` contained `576\n578\n641`). The R0 config already carried a
`ProcessLifecycleSelfVerifyProcessor` whose checklist explicitly warns about
lingering processes and wrapper PIDs — and both agents *read it and
self-reported compliance* without ever running the check. A text reminder
the model narrates past is not a fix; the model needs raw state it did not
author.

### Changes

- `processors/lifecycle_snapshot_verify.py` — new
  `LifecycleSnapshotSelfVerifyProcessor` (subclass of the stock
  `CustomSelfVerifyProcessor`, same singleton group `tb2_self_verify` so it
  replaces the R0 processor). On the first exit-intent turn it injects a
  **real `Bash` diagnostic tool call** dumping the live user-process table
  and the contents of every `*.pid`/`pid.txt` file, then injects a
  reconciliation + stock-checklist user message on the next `on_before_model`.
- `config.yaml` — swapped the R0
  `process_lifecycle_verify.py::ProcessLifecycleSelfVerifyProcessor` entry for
  the new processor (`file://` absolute path).

### Evidence

- `task_000140_01c78b42` final_pytest: `AssertionError: Lingering vm_service
  processes found: ['343', '606', '810']`. messages.json msg 23 agent
  narrates "Service stopped"; msg 26 self-reports every checklist item ✓ —
  never ran `pgrep -f vm_service`.
- `task_001090_c61c71f2` final_pytest: `pid.txt does not contain a valid
  integer PID ... '576\n578\n641'`. messages.json msg 52 `ps aux` shows the
  wrapper `bash -lc "... nohup ./monitor ... & echo $! > pid.txt"` (576) plus
  `./monitor` (578); msg 53 concludes "PID 578 is the actual monitor process"
  but never re-reads `pid.txt`, which still held three accumulated PIDs.

### Uncertainty

Risk 1: the injected Bash snapshot mis-fires in the run loop (e.g. tool-call
schema mismatch) and causes an error — mitigated by dry_fire/contract passing
and by using the same `Bash` tool the agent already uses. Risk 2: the agent
sees the snapshot but still doesn't act (behavior gap, not context gap) — if
so, R2 should escalate to a mechanical guard that blocks exit while duplicate
PIDs exist. Watch: does system_administration move off 0/5 and do the two
predicted tasks flip?

### Not harness-fixable this round (skip)

- task_000028_7fe033ac, task_000958_4bb2b05d: verifier import fails with
  `ModuleNotFoundError: No module named 'requests'` — the verifier depends on
  a package absent at agent phase; internet is blocked by default, so the
  agent cannot reliably stage it. Structural/verifier dependency, not a
  harness lever. Revisit only if env_probe shows pip/localhost mirror works.
- task_000010_644ab1c2: agent never created the required `/home/user/operator.py`
  despite 58 steps — a planning/capability gap, not a recurring harness
  mechanism (single task). Log, skip.

## Round 2 — force-action on truncation spiral

<!-- journal:frontmatter
round: 2
timestamp: 2025-08-17T10:00:00Z
hypothesis_id: h_truncation_forceact_v1
levers: [control]
predicted_affected: [task_001032_1adaccb9, task_000740_59416444, task_000010_644ab1c2, task_000028_7fe033ac]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=28/50; +1/-2 gained=task_001706_24462a09 lost=task_000396_e56917e2,task_000587_9862bb19; score 0.5600 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "Flips the length-truncation reasoning-spiral sub-cluster (whole-budget losses across system_administration / data_processing / data_querying); generalizes to any verbose over-reasoning small-model run that never reaches a tool call"
regression_risk: "One extra Bash round-trip + one user message only on runs already at the 3rd consecutive truncation (a state passing tasks never reach); snapshot cmd is fully generic with guards so it cannot error"
cost_shift: "Near-zero on healthy runs; net cost DECREASE on spiralling runs (bounded snapshot replaces empty 4096-token reasoning turns before they exhaust the budget)"
rollback_trigger: "If R3 pass_rate is flat/down vs R1 29/50 and the four cited tasks still show >2 truncation events each, or the injected snapshot causes any exit_reason=error in replay/eval, revert to the v4 length_recovery processor"
-->

### Why

R1 accepted at 29/50. Sweeping R1 failures surfaced a recurring,
harness-visible mechanism the R1 config's v4 LengthTruncationRecovery
Processor detects but cannot break: on four failing tasks
(task_001032 x14, task_000740 x9, task_000010 x4, task_000028 x4) the
model emits length-truncated pure-reasoning turns (tool_calls == 0,
finish_reason == length) turn after turn, never reaching a tool call.
The v4 collapse fires correctly (assistant turns end with its discard
marker) and v4's escalated text nudge ("write NO analysis, emit ONE
command") is present — and the model narrates right past it, exactly the
narrated-past failure the R1 lifecycle work already documented. Tasks
with only a single truncation event (000396, 000578, 001673, 001701)
all passed, so heavy truncation is a discriminating failure signal, and
the spiral consumes the whole step/token budget (001032 ran 1109s and
never fixed the tar parser; 000740 exited loop_detected; 000010 exited
budget_exceeded).

### Changes

- processors/length_recovery_forceact.py — new v5
  LengthTruncationRecoveryProcessor (same singleton group
  tb2_length_recovery, so it replaces the R1 v4 processor). Keeps v4
  collapse + escalating text nudge, adds a mechanical force_action_
  threshold (default 3): once that many consecutive truncations
  accumulate, on_after_model injects a real generic Bash workspace-
  snapshot tool call (pwd/ls -lat/find -mmin -30/ps, all guarded) so
  fresh tool output lands in context and breaks the reasoning-only
  momentum — the same injected-real-tool-call mechanism the R1 lifecycle
  processor proved fires in this harness. The injected call clears the
  streak, so it fires at most once per spiral. Also rewrote the repeat
  text nudge to steer toward small-payload edits (sed -i, chunked >>
  appends) instead of a single large heredoc, since a full-file rewrite
  cannot fit under the output cap and re-truncates.
- config.yaml — swapped the R0-asset v4 length_recovery.py entry for the
  new v5 processor (file:// absolute), knobs repeat_threshold=2,
  force_action_threshold=3, head_chars=400, tail_chars=0,
  content_char_threshold=40000. Every other pipeline entry byte-identical
  to R1.

### Evidence

- task_001032_1adaccb9 msg 22 assistant content ends exactly with the v4
  marker, tool_calls=0; 14 "cut off by the token limit" events over 76
  msgs; final_pytest missing extraction_log.txt (work never advanced).
- task_000740_59416444 msgs 58-71: LoopDetection "exact same tool call
  5..9 times" interleaved with truncation nudges; preceding assistant
  turns tool_calls=0; exit_reason=loop_detected.
- task_000010_644ab1c2 msgs 62-69: "I've been stuck in a loop" narration,
  tool_calls=0 truncated turns; exit_reason=budget_exceeded, 80 steps.
- task_000028_7fe033ac msgs 26/30/34/54: preceding assistant turns
  tool_calls=0 ending with the v4 marker. (Note: this task ALSO fails the
  verifier on a requests ModuleNotFoundError — the truncation fix is
  necessary but may not be sufficient here; see skip note.)

### Uncertainty

The forced snapshot cannot compute a correct answer — it only removes the
mechanical wall (no tool call ever executes) that guaranteed failure. If
the model reaches for the snapshot output but still can't solve the
underlying bug, the task stays failing (behaviour/capability gap, not
harness). Watch whether the four cited tasks drop below 2 truncation
events and whether any flip. If truncation counts drop but tasks still
fail, R3 should treat the remainder as capability gaps and stop investing
the control lever here.

### Not harness-fixable this round (skip)

- task_000028_7fe033ac, task_000958_4bb2b05d: verifier imports requests,
  absent at agent phase, internet blocked (confirmed again — agent never
  references requests/pip). Structural verifier dependency; C-001 may
  unblock the truncation half of 000028 but not the requests half. Skip
  the requests dependency until an env probe shows a local pip mirror.
- task_000010_644ab1c2: agent created /home/user/operator.py, which
  shadows the stdlib operator module and breaks every subsequent Python
  invocation from that dir (circular import in collections). C-001 may
  break its spiral, but the naming-collision reasoning error is a
  capability gap, not a recurring harness mechanism. Log, skip.
- task_000118_3043e92d: monitor daemon logic simply never limited log size
  (peak 209MB); correctness gap, not lifecycle/harness. Skip.

## Round 3 — no-op: remaining failures are capability gaps

<!-- journal:frontmatter
round: 3
timestamp: 2025-08-17T11:00:00Z
hypothesis_id: h_noop_capability_gaps_v1
levers: [control]
predicted_affected: []
gating_outcome: accepted
gating_attribution: score=29/50; +3/-2 gained=task_000587_9862bb19,task_001653_c4cafa73,task_001818_b251e5ea lost=task_001321_658ce4a8,task_001706_24462a09; score 0.5800 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "None claimed — explicit no-op. No shared harness mechanism remains across >=2 failing tasks; a mechanical process guard is proven unsafe (task_000118 legitimately runs 20 workers) and would regress passing service tasks (task_001090 keeps a single monitor running)."
regression_risk: "Zero — config is byte-identical to R2 incumbent."
cost_shift: "Zero."
rollback_trigger: "N/A (no change). Next round: only ship a lifecycle guard if a NEW round surfaces at least two lingering-process failures whose services are single-instance (so 'reap duplicates/zombies' is safe), or find a non-narrate-past mechanism."
-->

### Why

R2 scored 28/50 (56%). Swept all 22 failing tasks. Two structural
findings drove the no-op decision:

1. **Prior control interventions are near-inert.** The R2
   force-action truncation snapshot (force_action_threshold=3,
   consecutive) NEVER fired on any of its four target tasks
   (task_001032, task_000740, task_001818, task_001031 all show
   forced_snapshots=0) — truncations are scattered, not
   consecutive, so the streak never reaches 3. Its single R1->R2
   flip (task_001706) and two regressions (task_000396,
   task_000587) are noise-level (OVERVIEW warns same-config repeats
   vary by several tasks; task_000587 shows zero truncation and zero
   injection, so its regression is unrelated to the change).

2. **No shared harness mechanism remains.** The remaining failures
   are each a distinct model capability gap: SQL index plan
   (task_000264), CSV quoting (task_000536), centroid math
   (task_001653), Rust deadlock (task_001781), auth middleware 401
   (task_001498), grid-search value 50 vs 60 (task_001937), tar
   parser loop (task_001032), migration accuracy (task_000015),
   ticket-splitting regex (task_001818). Fixing any one requires
   domain knowledge, not a harness lever — per SOUL these are
   "model capability gaps, NOT the harness's job."

The one recurring-across-rounds harness-visible pattern —
lingering background processes (task_000140) — is now a SINGLE
task (n=1; no other R2 failure trips a pgrep/lingering/defunct
assertion). It fails the systemic-vs-idiosyncratic filter. And it
is NOT safely mechanizable: the R1 snapshot already fires and shows
the defunct PID (msg 25: 345 ... Z ... vm_service defunct), but
the agent narrates compliance anyway (msg 26 self-reports every
item OK) and then RE-RUNS test_pipeline.sh (msgs 28-31), spawning
fresh vm_service instances (603, 853, 1058) it never kills. That is
a behavioural narrate-past gap, not a context-availability gap — a
fresher snapshot would not change it. A mechanical kill-guard was
considered and rejected: task_000118 legitimately launches 20
worker processes and task_001090 (passing) requires a single
monitor to keep running, so any "reap duplicate/all instances"
guard would regress passing tasks. Pareto hard-invariant #6 forbids
a single-task win with proven collateral damage.

### Changes

- `config.yaml` — byte-for-byte copy of R2 incumbent (explicit
  no-op). Verified diff IDENTICAL; canonicalize ok.

### Evidence

- task_001032/000740/001818/001031: grepped messages.json —
  forced_snapshots=0 on all four R2 force-action targets; the R2
  control processor never fired. passive_trunc_markers present but
  non-consecutive.
- task_000140 final_pytest: Lingering vm_service processes found:
  ['345','603','853','1058']; messages.json msg 25 lifecycle
  snapshot fired showing 345 ... Z vm_service defunct; msg 26
  agent narrates all-correct; msgs 28-31 re-run test pipeline
  spawning more instances.
- task_000118 task text: "launches 20 worker processes" — proves a
  duplicate-instance reaper is unsafe/general-harmful.
- task_001090 (PASS) msg 52: "Monitor PID 588 is running" — a
  single long-lived service that a kill-guard would break.
- Distinct-capability failures confirmed via final_pytest tails
  (task_000264 USING INDEX, task_000536 CSV quoting, task_001653
  centroid, task_001937 grid 50 vs 60, task_001498 401 auth).

### Uncertainty

Risk of the no-op: leaving score at 56% when a bolder bet might
have paid off. Mitigated by the evidence that (a) the two prior
control bets were inert/noise, and (b) every candidate this round
was either n=1 with proven regression risk or a text nudge the
agent has twice demonstrated it ignores. If a future round's task
mix surfaces at least two single-instance lingering-process
failures, a zombie/defunct reaper (STAT contains Z, which no task
wants to keep) becomes a safe, generalizable Control candidate —
noted for next round.

## Round 4 — ensure verifier test-deps importable at exit

<!-- journal:frontmatter
round: 4
timestamp: 2025-08-17T12:00:00Z
hypothesis_id: h_verifier_dep_guard_v1
levers: [control]
predicted_affected: [task_000028_7fe033ac, task_000958_4bb2b05d]
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=30/50; +4/-3 gained=task_000015_89886d8d,task_000028_7fe033ac,task_000740_59416444,task_001706_24462a09 lost=task_000329_a3ac56b0,task_000587_9862bb19,task_001653_c4cafa73; score 0.6000 >= incumbent(mean) 0.5800 - tol 0.0400
expected_global_gain: "Flips the 'verifier imports a common test lib absent from the image' cluster (2 STUCK HTTP-microservice tasks whose test_final_state.py aborts at collection on import requests); generalizes to any HTTP-service task probed via requests/pyyaml"
regression_risk: "Near-zero: injected Bash imports each module first and pip-installs ONLY the missing ones (idempotent no-op on passing tasks); all steps guarded so it cannot error the run; fires at most once at exit-intent; order 95 gt lifecycle 90 so it never collides with that processor's snapshot"
cost_shift: "Plus one Bash round-trip and one short user turn only on runs reaching exit intent; dep-check output bounded/tiny. Negligible."
rollback_trigger: "If R5 pass_rate is flat/down vs R1 29/50 AND task_000028/task_000958 still show a ModuleNotFoundError No module named requests collection error, or the injected Bash call causes any exit_reason=error in replay/eval, revert."
-->

### Why

R3 scored 29/50 (58%) and was a no-op that concluded the remaining failures
were capability gaps. Re-sweeping the 17 STUCK (never-passed) tasks with fresh
eyes surfaced ONE recurring harness-visible mechanism the prior rounds
mis-classified. task_000028 and task_000958 both fail with the verifier's
test_final_state.py aborting at pytest collection time with
ModuleNotFoundError No module named requests then Interrupted 1 error
during collection. Both are HTTP-microservice tasks (a C++ server the
verifier probes over HTTP via the requests client). The agent's actual work
is complete and correct — task_000028's final tool turn shows
HTTP/1.1 200 OK ... 150 (correct frame count) with nginx + logrotate in
place — but the score is 0 solely because the hidden verifier module cannot
import a library the task never mentions. Prior rounds skipped these as
internet-blocked/structural. That premise is WRONG for this environment:
task_000684 msg#10 shows a real PyPI download (numpy-1.26.0 ... 58.3 MB/s),
so pip works and the missing dep is installable. Since agent and verifier
share the same container Python (proven by initial_pytest PASSing while
final_pytest fails only on the import), ensuring requests is importable
before exit makes the already-correct final state actually testable.

### Changes

- processors/verifier_dep_guard.py — new VerifierDepGuardProcessor
  (singleton group tb2_verifier_dep_guard, order 95). On the first
  exit-intent turn it injects a real Bash tool call that, for each package
  in a tiny generic allow-list of test-harness libs (requests, pyyaml),
  imports it and pip-installs ONLY if missing (all guarded), then a
  short user ack on the next on_before_model. Fires at most once per run.
- config.yaml — appended the new processor after the lifecycle self-verify
  processor; every other pipeline entry byte-identical to R3.

### Evidence

- task_000028_7fe033ac result.json final_pytest: ImportError while importing
  test module /tmp/test_final_state.py ... ModuleNotFoundError No module
  named requests ... Interrupted 1 error during collection. initial_pytest:
  4 passed (same container Python). Last agent tool turn:
  === Testing HTTP endpoint === HTTP/1.1 200 OK Content-Length 3  150 —
  task work complete.
- task_000958_4bb2b05d result.json final_pytest: identical
  ModuleNotFoundError No module named requests collection error;
  initial_pytest 2 passed. Task text: Write a C++ HTTP server listening
  exactly on 127.0.0.1:9090 ... Authorization header.
- task_000684_1a33ef37 messages msg#10 (tool): Collecting numpy==1.26.0
  Downloading numpy-1.26.0 ...whl (18.2 MB) ... 58.3 MB/s — pip + outbound
  network work here, so the exit-time pip install requests will succeed.

### Uncertainty

Risk 1: the pip install at exit could be slow or hit a transient network
error — mitigated by the guards (the tool call always succeeds) and by
the import-first check (no install attempted when already present). Risk 2:
these tasks may fail a further assertion once collection succeeds (the
verifier ran zero tests so far, so we only know the servers respond, not that
every endpoint/auth case is correct) — if collection succeeds but the tasks
still fail on a real assertion, the remainder is a capability gap and this
processor should stay (it is a safe idempotent no-op). Watch: do task_000028
and task_000958 lose the ModuleNotFoundError collection error next round?

### Not harness-fixable this round (skip)

- The other 15 STUCK tasks are each a distinct model capability gap confirmed
  by final_pytest tails (migration accuracy 000015, log size 000118, lingering
  procs 000140 n=1 unsafe per R3, SQL/CSV 000264, adversarial corpus 000505,
  CSV quoting 000536, graph edges 000740, Rust memory 000748, tarball 000933,
  tar-parser loop 001032, regression-test crash 001089, auth 401 001498, Rust
  deadlock 001781, grid value 001937, operator.py stdlib shadowing 000010). No
  shared harness mechanism across two-plus of these; each needs domain
  knowledge, not a lever. Skip.

## Round 5 — narration-loop hard-stop (cost containment)

<!-- journal:frontmatter
round: 5
timestamp: 2025-08-17T13:00:00Z
hypothesis_id: h_narration_loop_break_v1
levers: [control]
predicted_affected: []
cited_candidates: [C-001]
gating_outcome: accepted
gating_attribution: score=28/50; score 0.5600 >= incumbent(mean) 0.6000 - tol 0.0400 (final-round scoring)
expected_global_gain: "0 expected flips (target spirals are capability gaps). Positive Pareto move on cost/error-surface: terminates the 3-4 whole-budget reasoning spirals per round (~2000-3500 wasted steps, ~40+ min wall-clock) at a clean loop_detected exit, and converts task_001031's exit_reason=error into a graceful loop_detected."
regression_risk: "Near-zero. raise_threshold=6 is two full repeats above the max identical-narration streak of any PASSING R4 task (task_000740=4); streak resets on every tool-calling turn; one warn fires before any raise. No task in the 50-task set (pass or fail) repeated identical prose 6+ times except the doomed spirals."
cost_shift: "Net DECREASE. Zero effect on healthy runs (never reach 4 consecutive identical narrations). On spiralling runs cuts tens of steps and hundreds of seconds each."
rollback_trigger: "If R6 pass_rate is down vs R4 30/50 (any previously-passing task regresses to loop_detected on a real long task), or the new processor raises on a task whose narration streak is under 6, revert. Also revert if total round cost/wall-clock does NOT drop despite the target spirals still burning full budget (means the raise never fired - non-consecutive narration)."
-->

### Why

R4 scored 30/50 (60%). Swept all 20 R4 failures with fresh eyes. The
verifier-dep guard from R4 verifiably landed: task_000958 no longer aborts
on ModuleNotFoundError requests (4 tests now pass; its 3 remaining
failures are genuine SQL/endpoint logic bugs), and task_000028 flipped to
pass - so that mechanism is preserved (already in the incumbent config, no
new candidate needed). The remaining failures are each a distinct model
capability gap (CSV quoting 000536, delta-format blank lines 000329, F1
recall extraction 001321, adversarial bypass 000505, centroid 001653, Rust
deadlock 001781, grid 60-vs-50 001937, auth 401 001498, mpi4py API 001031,
tar parser 001032) - no shared harness mechanism flips 2+ of them, matching
R3/R4's conclusion. The ONE recurring harness-visible pattern is the
budget-exhausting reasoning spiral: 3-4 tasks (000396, 001031, 001032,
000010) burn the entire 80-step budget and 900-1100s while the stock
LoopDetectionProcessor fires 12-22 warn injections but never raises. Its
exact-match strategy never matches (Bash args are tweaked each turn) and its
name-only strategy is warn-only by design because tool-count is not a safe
discriminator here (passing tasks issue 37-48 consecutive Bash calls). The
discriminating signal these spirals share is repeated near-identical
assistant narration - a fingerprint the existing processor does not compute.

### Changes

- processors/narration_loop_break.py - new NarrationLoopBreakProcessor
  (singleton group tb2_narration_loop_break, order 22, just after stock
  loop_detection at 20). Tracks consecutive assistant turns whose normalized
  content (lowercased, digits/punct stripped, whitespace-collapsed, 220-char
  prefix) is near-identical; resets on any tool-calling turn. At
  warn_threshold=4 injects one concrete warning; at raise_threshold=6 raises
  LoopDetectedError -> clean exit_reason=loop_detected.
- config.yaml - appended the new processor after LoopDetectionProcessor;
  every other pipeline entry byte-identical to R4.

### Evidence

- task_001031_a8f0eb37: three consecutive assistant turns verbatim "I've been
  stuck in a loop trying to fix the mpi4py Allgatherv API ... let me try a
  fundamentally different approach"; interleaved tool turns show the identical
  ValueError message expecting 2 to 4 items traceback; exit_reason=error,
  42 steps. Max identical-narration streak measured = 8.
- task_000396_e56917e2: 30 assistant turns with the same "stuck" narration, 22
  LoopDetection warn injections, 11 truncation markers, 80 steps,
  budget_exceeded; final_pytest still assert 0.0 < 0.0. Streak = 6.
- task_001032_1adaccb9 / task_000010_644ab1c2: 80 steps, budget_exceeded, 11-12
  LoopDetection warnings ignored; underlying bug never fixed.
- Regression-safety sweep (all R4 PASSING tasks): max identical-narration
  streak on any passing task = 4 (task_000740, PASS); typical passing long
  tasks (000015, 000028) capped at 3. raise_threshold=6 sits two repeats
  above every passing task -> zero measured regression surface.

### Uncertainty

The raise only fires if the near-identical narrations are consecutive - the
same failure mode that made R2's force-action snapshot inert (R3 found
truncations were scattered, streak never reached 3). If these spirals'
narrations are interleaved with materially-different turns, the streak resets
and the raise never fires (harmless no-op, but no cost gain). The R4 bodies
show the narration IS repeated verbatim back-to-back (001031 shows 3 in a row
in the last 6 messages), so the consecutive assumption holds here better than
it did for truncation. This is a cost/error-surface bet, NOT a pass-rate flip
- predicted_affected is intentionally empty. Watch: does round wall-clock/cost
drop, do the four spiral tasks exit loop_detected instead of
budget_exceeded/error, and does NO previously-passing task regress.

### Not harness-fixable this round (skip)

- The ~13 other STUCK/failing tasks are each a distinct capability gap
  (per-task final_pytest tails confirm: CSV quoting, delta blanks, F1 recall,
  adversarial bypass, centroid math, Rust deadlock, grid value, auth 401,
  log-size cap, tarball contents). No shared harness lever across 2+. Skip.
