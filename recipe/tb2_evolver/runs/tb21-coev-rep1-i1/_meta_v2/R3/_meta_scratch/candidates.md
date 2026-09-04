# Candidates — R3

Baseline: R2 config scored 4/18 (pass_rate 0.2222). R1's context-overflow guard
and R2's repeatable act-loop keepalive both landed and are load-bearing — the
previously instant-dying / prose-stalling tasks now run 20-100 Bash steps. The
new dominant failure signature in R2 trajectories is NOT instant death or a
single prose stall: it is **the required output file the task explicitly names
is never written to its exact path**, even after the agent has burned dozens of
steps. Six of the fourteen failing tasks fail the verifier's *first* check —
"output file exists" — with `FileNotFoundError`.

## Candidate C-003
[lens: failure | lever: control | intent: corrective]

Evolve the R2 keepalive into an **output-artifact-aware** keepalive: extract the
output file path(s) the task description explicitly asks the agent to
create/save/write, and when the agent stalls (no-tool-call terminal turn), the
continuation nudge names the exact declared output path(s) still unaddressed
instead of only generic "act via Bash" guidance.

- Tasks affected (corrective, same mechanism — declared output path never
  created, verifier fails on file-exists check):
  - regex-log — `/app/regex.txt` never written
  - feal-linear-cryptanalysis — `/app/plaintexts.txt` never written
  - large-scale-text-editing — `/app/apply_macros.vim` never written
  - protein-assembly — `/app/gblock.txt` never written
  - sam-cell-seg — `/app/test_output.csv` never written
  - vulnerable-secret — `/app/results.txt` never written
- Signal: verifier `test-stdout.txt` first failing assertion is
  `FileNotFoundError: [Errno 2] No such file or directory: '/app/<X>'` /
  `assert PosixPath('/app/<X>').exists()`; the episode ends on a large
  text-only assistant turn (`tool_calls=[]`) that reasons about the answer but
  never runs a Bash command writing the declared path.
- Verified (Read):
  - regex-log — final segment episode `raw_assistant` turn is 113,297 chars of
    prose reasoning about the regex ("Let me think about this more carefully:
    1. For the date pattern: ...") with `tool_calls=0`; `/app/regex.txt` never
    created. Task text (grepped from trajectory) explicitly: "Save your regex in
    /app/regex.txt". Grep count of `/app/regex.txt` in episode = 14 (agent knew
    the path yet never wrote it).
  - feal-linear-cryptanalysis — episode ends `done` on an 11,377-char summary
    turn describing a failed brute-force; the required `/app/plaintexts.txt`
    is never mentioned anywhere in the trajectory (agent worked on
    `/app/pairs.txt`, `/app/feal.c`, `/app/attack.c` and lost the deliverable).
  - vulnerable-secret — episode: L50 is a 45,298-char text-only assistant turn,
    L51 a 184-char "previous response was cut off. Let me continue... XORing
    with 0x42" turn, then `exit_reason=error`; `/app/results.txt` never written.
  - large-scale-text-editing — only 5 steps then `FileNotFoundError:
    '/app/apply_macros.vim'`; task text names `/app/apply_macros.vim` explicitly.
  - protein-assembly (79 steps) / sam-cell-seg (50 steps) — both end without
    the declared `/app/gblock.txt` / `/app/test_output.csv`; verifier fails
    file-exists first.
- Why Control not Instruction: the required-output path is *dynamic per-task
  context* (it lives in the task description and differs every task) that the
  agent provably stops tracking mid-run — regex-log rambled 113k chars without
  writing the path it had cited 14 times; feal never referenced the output path
  at all. A static system-prompt rule ("verify outputs exist before finishing")
  is exactly what the R2 nudge already says generically, and it did not fix this
  cluster. A Control hook can re-inject the *specific* extracted path at the
  decisive terminal-stall step, which a static prompt cannot. Not Action: TB2
  gives the agent exactly one tool (Bash) and it is a hard benchmark limit
  (playbook) — no new tool is addable; the file-write capability already exists,
  the agent just fails to exercise it on the declared path.
- Why evolve/replace the R2 keepalive rather than add a parallel processor:
  the R2 keepalive already owns the terminal-stall intercept (injects a synthetic
  keepalive tool call + queues a generic nudge). A second processor intercepting
  the same stall would double-inject synthetic tool calls / nudges and break the
  +1 message-insertion contract. The clean, no-collision move is a single
  superset processor that keeps R2's exact repeatable-bounded-keepalive contract
  and enriches only the nudge text with the extracted declared output path(s).
- Retroactive check (A-corrective): yes for regex-log / large-scale-text-editing
  / vulnerable-secret — at the terminal stall the agent had a candidate answer
  in its reasoning; a nudge naming "you have NOT yet created /app/regex.txt —
  write your current best answer to that exact path via Bash now" gives it a
  concrete commit action it repeatedly failed to take on its own. Partial for
  protein-assembly (hit a network wall for PDB lookup — capability gap; a nudge
  may still produce a best-effort file but correctness is not guaranteed) and
  feal (hard cryptanalysis — capability gap on correctness, but writing *some*
  plaintexts.txt lets partial tests run). The nudge is generic strategy
  (name-the-declared-output), no domain knowledge embedded, so no downside on
  the capability-gap tasks beyond a few extra bounded steps.
- expected_global_gain: targets the single largest failing cluster (6 tasks all
  failing the verifier's file-exists precondition); flipping even 2-3 of the
  "answer-in-reasoning-but-uncommitted" tasks (regex-log, large-scale-text-
  editing, vulnerable-secret) is a material pass-rate move, and the mechanism
  generalizes to any future task that declares an output path.
- regression_risk: low — the 4 passing tasks (configure-git-webserver,
  count-dataset-tokens, git-leak-recovery, kv-store-grpc) never hit the terminal
  stall branch (their winning turns carry tool calls, verified in R2 journal),
  so the nudge never fires for them. The completion-sentinel escape hatch
  (SUCCESS) is preserved byte-for-byte from R2 so a genuinely-finished agent
  still stops. Path extraction is conservative (only paths adjacent to
  save/write/create/output verbs); if none extracted, behaviour degrades exactly
  to R2's generic nudge — a strict superset, never worse than R2.
- cost_shift: neutral-to-slightly-up — same bounded `max_reprompts` budget as
  R2; the specific nudge tends to end the loop *sooner* (agent writes the file
  and can then legitimately SUCCESS) rather than adding turns.
