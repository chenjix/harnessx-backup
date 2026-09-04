# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a `BackgroundProcessPersistenceGuard` that appends a one-time advisory to
a Bash tool result when the command backgrounds a job (`&` / `nohup`),
explaining that background jobs do not persist across separate tool-call
shells and giving the two correct patterns (detach with `setsid`/`disown`, or
co-run launcher + workload in one Bash call).

- Tasks affected: task_000118_3043e92d, task_000010_644ab1c2
- Signal: both `exit_reason=budget_exceeded` at the 80-step cap; both launch a
  helper process in the background then act on it in a *later* tool call.
  task_000118 `bg-launch=3`, `tokencutoff=16`-style repeated narration; the
  agent loops on `ps aux | grep deployment_monitor` finding nothing.
- Verified (Read of messages.json):
  - task_000118 step 13: `rm -f /home/user/logs/*.log; python3 /home/user/deployment_monitor.py &`
    launched in one call; step 15/16 `sleep 0.5 && ps aux | grep ... deployment_monitor`
    returns `(exit 1, no output captured)` — process gone. Steps 19-65 are a
    repeated "monitor isn't running / let me try again" loop (`ps aux`, re-launch,
    re-launch) that never realises the launch shell already exited. Budget
    exhausted at step 80 with logs at full 200 MB (verifier: peak 209715200 >
    45000000).
  - task_000010 step (nohup): `cd /home/user && nohup python3 mock_api.py > /tmp/mock_api.log 2>&1 & sleep 1 echo "Mock API started"`
    starts a mock API in the background; subsequent `cd /home/user && python3 operator.py`
    runs in a *separate* tool call, so the operator cannot reach the API the
    agent believed was up. `exit_reason=budget_exceeded`.
- Why Control not Instruction: the persistence boundary is a structural fact of
  the sandbox (each Bash call is an independent `docker exec bash -lc`), not
  something the agent can read from the task or the workspace. A prompt rule
  applies unconditionally and dilutes; a Control hook fires *exactly at the
  moment* the mistake is made (right after the offending launch), so the
  corrective context is adjacent to the action — the highest-signal placement.
  It also only triggers on the specific `&`/`nohup` shape, so tasks that never
  background a process pay nothing.
- Why Control not Configuration: no existing knob encodes "explain the shell
  boundary on a background launch"; the length-recovery / loop-detection knobs
  address the *symptom* (repetition) not the *cause* (invisible persistence).
- Retroactive check (A-corrective): yes — had the advisory been present at
  task_000118 step 15 (first `ps` returning empty after the `&` launch), the
  agent would have had the persistence boundary named in-context at the decisive
  step, redirecting it to `setsid ... & disown` or to a single-call
  launch+deploy+inspect, instead of 40+ steps of blind re-launching. Same for
  task_000010's mock-API-then-operator split.
- expected_global_gain: closes the daemon/monitor/background-service failure
  shape in the `budget_exceeded` cluster (≥2 tasks). Generalises to any task
  that starts a service and then interacts with it — a recurring
  system_administration pattern.
- regression_risk: low. The processor only *appends* to a Bash result and only
  when a background launch is detected, capped at `max_fires=2` per task. It
  never blocks, rewrites, or removes a command, and never touches
  `event.messages` (contract-neutral). Worst case: two short advisory notes on
  tasks that legitimately background a job and already got it right — a few
  hundred tokens, no behavioural harm. `setsid`/`disown` commands are exempted
  so already-correct detaches are silent.
- cost_shift: negligible-to-negative. At most 2 short notes (~150 tokens each)
  per affected task; expected to *save* tokens by cutting the multi-dozen-step
  re-launch loops that currently run to the 80-step budget cap.
