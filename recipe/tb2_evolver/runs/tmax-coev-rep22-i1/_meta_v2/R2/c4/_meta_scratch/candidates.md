# Candidates — R2 / c4

Assigned focus: `task_000140_01c78b42` (system_administration) fails.

## Diagnosis (assigned task)

Task: fix a Go VM-provisioning service + supervisor script + build a CI/CD
`test_pipeline.sh` that starts the service, POSTs a request, then
**gracefully stops** it. Verifier asserts (`test_no_lingering_service_processes`)
that `pgrep -f vm_service` returns **nothing** after the agent exits.

Result: reward=0, finished=`no_tool_calls`, 22 steps. The agent's code was
correct (main.go, start_service.sh, test_pipeline.sh all right; vm_setup.log
got the expected line). It failed purely on **final process state**: verifier
found lingering PIDs `342, 595, 849, 1075, 1274`.

Mechanical cause (from body):
- The one-shot lifecycle self-verify nudge (this lineage's R1 fix,
  `LifecycleSelfVerifyProcessor`) **did fire** (msg 213). Its cleanup branch
  explicitly says: "if you ran a start-then-stop pipeline, confirm no matching
  process is left behind — `pgrep -f <name>` must return nothing".
- The agent read the nudge, ran `pgrep -f vm_service` (msg 279) → found `342 383`.
  Instead of a single clean teardown, it **re-ran `test_pipeline.sh` several more
  times** (msgs 319, 339, 379, 479). Each run does `./vm_service &` then
  `kill -TERM $PID` on only the *latest* PID — so orphans from earlier runs
  (incl. the very first PID 342, started before any pid file) accumulated.
- Its `pkill -f vm_service` calls returned exit 143/137 (the pkill subshell got
  signalled) and never confirmed a clean `pgrep`. Its **final** tool action
  (msg 479) started a fresh `vm_service &`, and it exited (msg 511, no tool call)
  with services still running.

So: right code, wrong exit hygiene. The agent treated "re-run the pipeline" as
verification; re-running a start-then-stop pipeline *starts* the service again.

## Why no blunt mechanical guard (Pareto rejection)

I considered a Control processor that detects background-start commands
(`&`/`nohup`/`systemctl start`) and re-arms the teardown nudge / blocks exit.
Rejected on measured regression surface:

- Background-start commands appear in **many passing tasks** (task_000329,
  000338, 001090, 001264, 001321, 001591, 001673, 001697, 001706, 001761 …).
- Counting background-starts *after* the self-verify nudge fired: **13 passing
  tasks** issue one (000338, 000740, 000818, 000965, 001090, 001264, 001498,
  001591, 001652, 001673, 001697, 001706, 001761). These are legitimate
  **keep-alive** services that the verifier wants *still running*.
- A guard that can't read task intent from Bash alone would nudge those 13
  passers to "confirm no process lingers / pgrep must be empty" — which risks
  making them **kill a service they must keep alive**. Net-negative Pareto.

The keep-alive vs teardown distinction is task-semantic and unreadable from
Bash-only observation. This is why the existing nudge carefully carries *both*
branches. The failure is therefore mostly a model behavioral gap, but there is
one zero-new-trigger, keep-alive-safe refinement available (C-001).

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Strengthen the **cleanup/teardown branch** of the already-firing one-shot
`LifecycleSelfVerifyProcessor` addendum with a general warning against the
observed anti-pattern: *re-running a start-then-stop pipeline is not a clean
verification — it restarts the service; your final action on a teardown task
must be a teardown whose success you confirm with an empty `pgrep`, and killing
one saved PID can miss orphans from earlier runs.*

- Tasks affected: task_000140_01c78b42 (assigned). Single-task grounded — see
  Pareto note; the change is scoped to add **zero new triggers** and carries
  no keep-alive risk, so it is a safe corrective even at n=1.
- Signal: `final_pytest` `test_no_lingering_service_processes` AssertionError
  "Lingering vm_service processes found"; body shows nudge fired then agent
  re-ran the pipeline and exited dirty.
- Verified (Read): msg 213 self-verify tool fired; msg 279 `pgrep -f vm_service`
  → `342 383`; msgs 319/339/379/479 re-run `test_pipeline.sh` (each `./vm_service &`);
  msg 511 final assistant message, no tool call, services still up.
- Why Control not Instruction: the text lives inside an existing custom
  processor (`LifecycleSelfVerifyProcessor`) that this lineage already owns and
  that fires mechanically at the exit event — I am editing that processor's
  injected string, not the system-prompt template. It rides the existing
  one-shot exit-detection hook (no new firing surface). A system-prompt rule
  would fire on *every* task from step 0 and dilute; the processor scopes the
  reminder to the exit moment where it is decision-relevant.
- Why NOT a new re-arming/exit-blocking guard: measured 13 passing keep-alive
  tasks would be falsely nudged toward killing live services (see Pareto note).
- Retroactive check (A-corrective): partial-yes. The nudge already fired and
  was ignored once, so this is not a guaranteed flip. But the ignored nudge did
  not name the *specific* trap the agent fell into (re-run-as-verification /
  orphan accumulation / confirm-with-empty-pgrep). Naming it directly is the
  minimal defensible edit for this failure; if the model still ignores explicit,
  trap-named guidance, the residue is a pure capability gap (logged in memo).

expected_global_gain: teardown/cleanup class (system_administration lifecycle
tasks graded on empty `pgrep` at exit). Generalizes to any "start-then-stop
pipeline" task; the guidance is domain-agnostic (no task ids, ports, paths,
binary names).

regression_risk: near-zero. Edit only extends text inside the existing one-shot
cleanup branch; adds no new trigger, no new hook, no exit block. Keep-alive
tasks read the *keep* branch (unchanged) and are untouched. Worst case: a few
extra tokens in the one nudge that already fires.

cost_shift: negligible (+~60 tokens on the single self-verify injection that
already happens once per task; zero extra turns).

rollback_trigger: if any currently-passing keep-alive service task regresses
(e.g. a task that must leave a daemon running flips to failing because the agent
killed it), revert.
