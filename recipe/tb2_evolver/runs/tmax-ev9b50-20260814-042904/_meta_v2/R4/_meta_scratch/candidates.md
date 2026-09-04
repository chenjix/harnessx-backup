# R4 Candidates

## Candidate C-004 — anti-loop / break-the-repeat discipline (system prompt)

**Three-axis tag:** lens=control-flow / lever=instruction / intent=close-failing-cluster

**Signal (verified from R3 trajectories):**
The single largest *harness-addressable* cluster in R3 is a runaway
identical-command repeat loop that consumes the entire step budget or grows
context until the model server 400s. Measured per-task max-consecutive-identical
Bash commands (from `<task>.messages.json`):

| Task | exit_reason | max consec identical | looping command |
|------|-------------|---------------------:|-----------------|
| task_000010_644ab1c2 | max_steps (80) | 39 | `python3 -c "import importlib._bootstrap_external; ..."` |
| task_000028_7fe033ac | max_steps (80) | 43 | `wait 100; sleep 2; ps -ef | grep -E "(nginx|server)"` |
| task_000118_3043e92d | max_steps (80) | 54 | `python3 /home/user/deployment_monitor.py & sleep 1; ps -ef` |
| task_001701_95e3bbcb | max_steps (80) | 45 | `.../jshon -F .../1.json -e 0 -k cert_b64 | head -c 500` |
| task_000015_89886d8d | error (HTTP 400 @ step 75) | 59 | identical repeat grew context → upstream 400 |
| task_001652_86e1d185 | error (@ step 16) | 6 | identical repeat |
| task_000505_50b5162d | error (@ step 31) | 8 | identical repeat |

All 7 are FAILs. Contrast: among the 30 passing tasks, max-consecutive-identical
is ≤3 for every task **except** task_000684_1a33ef37 (PASS), whose 42-repeat
"loop" is a harmless `echo "Task completed successfully!"` *after* the artifact
was already correct — aborting that loop earlier would not have changed its pass
and would have saved ~40 wasted steps.

**Verified body evidence:**
- task_000010: agent created `/home/user/operator.py` (required by the task spec),
  which shadows Python stdlib `operator`; every subsequent `python3` invocation
  from `/home/user` fails, and the agent issues the *identical* debug one-liner
  ~39 times in a row without recognizing the root cause, hitting max_steps.
- task_000028 / task_000118: agent re-runs the *same* `ps`/`sleep` poll dozens of
  times expecting a background service to appear, never changing approach.
- task_000015: the identical-repeat loop inflates the message history until the
  upstream model server returns `HTTP Error 400` at step 75 and the whole
  session is discarded — i.e. the loop directly *causes* an out-of-surface crash
  that a break-the-loop directive could have averted.

**Root cause (harness deficiency, not capability gap):** the R1 loop-detection
*processor* is inert on this eval loop (the tmax run loop
`recipe/tmax_eval/agent_loop.py::run_agent` ignores the config.yaml processor
pipeline and only consumes the resolved system prompt string — re-verified this
round by reading the loop source). The only live lever is the system prompt.
The built-in / current prompt says nothing about detecting that repeated
identical commands with identical results is a no-progress signal, so the model
falls into deterministic (temperature=0) repeat loops.

**Intervention:** keep the accepted R3 adversarial verify-before-exit prompt
unchanged and *add* a concise, general "NO-PROGRESS / BREAK THE LOOP" discipline
section: if the same (or a trivially-varied) command has produced the same
result ~2–3 times, that is a no-progress signal; the agent must stop repeating,
step back to diagnose the root cause (inspect logs/errors, question its
assumptions — e.g. a file it created may be shadowing a system module, a
background service may be silently dying), and either change strategy or, if it
cannot make progress, stop cleanly rather than burning the budget. No task
literals — pure general control-flow strategy.

**Retroactive check (variant: would-this-have-helped):**
For the 4 max_steps loop tasks, breaking the loop at ~3 repeats returns ~35–50
steps of budget the agent could spend diagnosing the real bug (e.g. discovering
the `operator.py` shadow, or that the service dies). For task_000015 it avoids
the context-growth-induced HTTP 400 entirely. Even where the underlying task is
a true capability gap and still fails, the run fails *cheaper* and does not
crash the session. Net: ≥0 pass flips with high upside, guaranteed cost win.

**Why instruction, not configuration/control (the lever argument):**
The natural fix is the `LoopDetectionProcessor` (control lever) — but it is
provably inert here (agent_loop.py does not run the pipeline; confirmed by
reading the source and by R1's flat result). The tool set is fixed to a single
Bash tool (action lever unavailable — TB2 playbook + agent_loop source). The
system prompt is the *only* mechanism that reaches the live loop, so the
control-flow fix must be delivered as an instruction. This is a genuinely new
hypothesis (control-flow / anti-loop), distinct from the R2/R3 verify-before-exit
instruction line, so it does not re-propose any reverted hypothesis.

**expected_global_gain:** targets the 7-task runaway-repeat cluster (4 max_steps
+ 3 error). Even partial recovery on the max_steps loops flips failing tasks;
generalizes to any future task where the model would otherwise deterministically
repeat a stuck command.

**regression_risk:** Low. Passing tasks show max-consecutive-identical ≤3 except
the benign echo case (task_000684) where an early abort is harmless (artifact
already correct). The directive is advisory ("this is a no-progress signal, step
back") not a hard cap, and it explicitly says to change approach *or* stop — it
does not forbid legitimate short polling loops (poll 2–3 times is fine). It is
appended to the already-accepted R3 prompt, so the verify-before-exit behavior
that holds the 28–30 band is preserved.

**cost_shift:** Strongly negative (cheaper). The 4 max_steps loop tasks each burn
the full 80-step budget on identical commands; breaking at ~3 repeats reclaims
30–50 steps/task. task_000015 avoids a 74-step run that ends in a discarded
session. No added cost on passing tasks (they do not loop).

**rollback_trigger:** If R5 pass_rate drops below the 28–30 band, or any
currently-passing task regresses to a premature stop attributable to the anti-loop
directive (agent stops after a legitimate 2–3x poll before finishing), revert to
the R3 prompt.
