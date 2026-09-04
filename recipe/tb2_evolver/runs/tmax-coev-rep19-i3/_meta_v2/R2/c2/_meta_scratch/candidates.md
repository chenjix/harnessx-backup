# Candidates — R2/c2

## Candidate C-001 — ShellCwdGuard (non-persistent-shell / cwd-reset trap)

**Three-axis tag:** lens=execution-mechanics / lever=control / intent=fix-recurring-failure-mode

**Assigned focus:** `task_000028_7fe033ac` — nginx reverse-proxy → C++ UNIX-socket
video backend. `budget_exceeded` at 80 steps / 574s, reward 0, verifier 502
Bad Gateway (nginx cannot reach the backend because the backend is not running).

### Signal (verified from trajectory body)

`task_000028_7fe033ac.messages.json`:
- The agent iterated correct C++ (heredoc-wrote `/app/server.cpp` using
  `popen`+`atoi`), compiled it successfully: step [66] `g++ -o /app/server
  /app/server.cpp -std=c++11 2>&1` → tool result `(exit 0, no output captured)`.
- Then it ran the binary by RELATIVE path: step [68] `./server >> /app/server.log
  2>&1 & sleep 1; cat /app/server.log`. Tool result [69] contains
  `bash: line 1: ./server: No such file or directory` (twice at [69]).
- Same at step [63]: `bash: line 34: ./server: No such file or directory`.
- The `stoi`/`Error parsing frame count:` lines at the TOP of those results are
  **stale append-only content** of `/app/server.log` from the very first broken
  binary — `cat server.log` keeps replaying old lines, which compounded the
  agent's confusion (it concluded "the server is still running old code").
- Root cause verified in code: `recipe/tmax_eval/docker_sandbox.py::exec` runs
  every command with `workdir=self._workspace_path` — a fresh `docker exec` each
  turn. There is NO persistent shell; `cd`/relative paths reset every turn. The
  `/app/server` binary exists (`EnvironmentContextInjector` shows workspace =
  `/home/user`, not `/app`), but `./server` resolves against the workspace root.
- `EnvironmentContextInjector` integrity/sandbox reminders cover data
  fabrication, service-killing, and background-process non-persistence — but say
  NOTHING about cwd/shell non-persistence. Gap confirmed.

### Why this is a harness deficiency, not a capability gap

The error text `./server: No such file or directory` looks like a missing file,
not a cwd problem; the agent cannot infer non-persistence from it and has no
prior telling it the shell resets each turn. A weaker model reads the stale log
and mis-attributes the failure to its code. The mechanism (fresh exec per call)
is a harness fact the agent is never told — classic "agent lacks dynamic context
it needs" → inject at runtime.

### Change

New processor `processors/shell_cwd_guard.py::ShellCwdGuard` (mirrors the
contract-safe `RepeatedCommandBreaker` shape: augments `event.result` only,
never inserts messages). On `on_before_tool` it flags Bash commands that rely on
non-persistent state (relative executable/script invocation `./x` or
`python app.py`, or a standalone `cd` not chained with `&&`), and on
`on_after_tool` — if that command's output carries a `No such file or directory`
/ `command not found` signature — appends ONE corrective nudge (bounded
`max_fires=2` per task) explaining the shell is non-persistent and to use
absolute paths or chain `cd DIR && cmd`. Commands that already self-chain
(`cd /abs && ./prog`) are never flagged.

### Retroactive check (variant: would-it-have-fired)

On task_000028: step [68]/[72] issued `./server ...` (no chained cd) → result
contained `No such file or directory` → guard fires the nudge on the first such
result, telling the agent to run `/app/server` (absolute) or `cd /app && ./server`.
With the binary actually launching, the UNIX socket comes up and nginx proxies
200 instead of 502. It fires at most twice, so it does not spam.

Would-NOT-fire safety: on a command that self-chains `cd /app && ./server`, or
any command without a relative-invocation/standalone-cd, `_looks_like_cwd_trap`
returns False → no nudge. On a genuinely missing file with no relative-path
reliance, no nudge (correctly — that IS a real missing file).

### Why control (runtime injection) not instruction (static prompt)

A static system-prompt line ("the shell is non-persistent") would fire on 100%
of tasks and add noise/tokens to every run including the many that never touch a
relative path. The runtime guard fires ONLY when the trap actually manifests
(command shape + matching error), so it is high-signal, near-zero cost on
unaffected tasks, and self-documents the fix at the exact moment the agent is
confused. This mirrors the accepted R1 control-lever pattern.

### Pareto statement

- `expected_global_gain`: closes the non-persistent-shell/cwd cluster — any
  compile-and-run, build-a-service, or `cd`-then-run task where the agent
  assumes an interactive terminal. task_000028 is the clearest instance; the
  shape recurs whenever a binary/script is invoked by relative path.
- `regression_risk`: LOW. The nudge only appends text to a tool result that
  ALREADY errored, and only when the command relied on non-persistent state.
  Passing tasks that use absolute paths or self-chained `cd` are never touched.
  Worst case: a real missing-file error that also used `./x` gets one extra
  (still-correct) reminder to verify the path with `ls -l` — harmless.
- `cost_shift`: DOWN on affected tasks (agent stops looping on a phantom
  missing-file bug and reaches the fix, freeing budget); negligible on others
  (bounded to ≤2 short appends, only on erroring commands).

`rollback_trigger`: a previously-passing task newly fails and its trajectory
shows a `[ShellCwdGuard]` nudge on a command that was legitimately using a
relative path that DID exist in the workspace root (false positive); then tighten
`_looks_like_cwd_trap` or lower `max_fires` to 1.
