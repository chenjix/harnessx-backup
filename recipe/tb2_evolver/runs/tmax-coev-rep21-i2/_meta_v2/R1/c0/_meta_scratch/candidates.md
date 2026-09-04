# Candidates — Round 1 (c0)

Assigned focus: `task_000010_644ab1c2` fails (`reward=0`,
`exit_reason=budget_exceeded`, 80 steps). Diagnosis below, then the
systemic candidate the failure exposes.

## Diagnosis of the assigned task

Task requires writing `/home/user/operator.py`. That filename shadows
Python's stdlib `operator` module. Because CWD (`/home/user`) is
`sys.path[0]`, every `python3` invocation resolves `import operator`
(transitively imported by `collections` → `functools` → `re` → ...)
to the agent's own script, producing a circular-import crash that
poisons the entire interpreter — including the pytest verifier.

The model *correctly identified the root cause* ("Python's import
system finds my operator.py") but never applied the fix (`python3 -P`,
`PYTHONSAFEPATH=1`, or running from a different CWD). Instead it entered
a tight behavioural loop: `ln -s ... operator.py` → run → circular
import → `rm && mv` → run → same error → repeat, ~10 cycles, burning
all 80 steps. Knowing the *specific* fix is a **model capability gap**
(logged in the journal, no prompt-embedded fix). But the *loop that
wasted the whole budget with zero corrective feedback* is a **harness
deficiency** — and it recurs across the whole `budget_exceeded`
cluster, not just this task.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add the existing `LoopDetectionProcessor` to the pipeline (not
currently installed), tuned to warn on the 3rd consecutive identical
Bash command and raise `LoopDetectedError` on the 8th — escalating
redirect nudges before termination.

- Tasks affected (same mechanism — verbatim consecutive Bash-command
  repetition burning the 80-step budget):
  - `task_000010_644ab1c2` (assigned): multi-command cycle, 5× repeats
    of `ln -s .../operator.py`, 5× `cat > operator_script.py`, 4× `rm && mv`.
  - `task_001031_a8f0eb37`: **29 consecutive identical** `python3 -c "from mpi4py import MPI; help(...)"`.
  - `task_000011_d089ef35`: **27 consecutive identical** `kill -9 66 ...; ps aux | grep mesh_server`.
  - `task_001382_6c9d34ea`: **26 consecutive identical** `kill -9 -p 72 ...`.
  - `task_000477_de422e4d`: **26 consecutive identical** `sha256sum manifest...`.
  - `task_000313_1dce9844`: **18 consecutive identical** `python3 -c "import urllib.request..."`.
  - `task_001207_44e97fe1`: **13 consecutive identical** `xxd .../log_sanitizer.elf | head -200`.
  - (plus `task_000118`, `task_000510`, `task_000683`, `task_000958`,
    `task_001857` with similar high-repeat / 2-cycle thrash.)
- Signal: `exit_reason=budget_exceeded` on 12 of 50 R0 tasks, all at
  exactly 80 steps; per-trajectory command histograms show one command
  repeated 13–29× verbatim and consecutively. No processor in the R0
  config detects action-level (tool-call) loops — the installed
  `LengthTruncationRecoveryProcessor` only handles the *different*
  shape of `finish_reason=length` with no tool call.
- Verified (Read, task_000010 messages.json): steps 2→68 alternate
  `ln -s .../operator.py` (msg 2,17,53) → `python3 ...` → circular-
  import Traceback (msg 5,10,20,24) → `rm && mv` (msg 6,21,33) — the
  identical (command → identical error) cycle repeats with no
  intervening corrective signal; assistant narration from msg 25 on is
  literally "I've been stuck in a loop" yet keeps issuing the same
  calls. Verified (script over messages.json): task_001031 has 29
  consecutive identical `python3 -c "from mpi4py..."` calls;
  task_000011 has 27 consecutive identical `kill -9 66...` calls;
  task_001382 has 26 consecutive identical `kill -9 -p 72...` calls.
- Why Control not Configuration: there is no existing knob to turn —
  the loop-detection component is simply **absent** from the R0
  pipeline. Adding a processor with tuned kwargs is the minimal
  structural change; no new code authored (reuses the vetted
  `harnessx.processors.control.loop_detection.LoopDetectionProcessor`).
- Why Control not Instruction: the model already *narrates* awareness
  of the loop ("I've been stuck in a loop") and still repeats the
  exact command — a prompt rule telling it "don't loop" cannot fire
  when the model won't act on its own stated realisation. A mechanical
  hook that injects an escalating warning into the tool result at each
  repeat, and hard-stops at 8, is the only thing that changes the
  in-context signal the model conditions on.
- Retroactive check (A-corrective): partial-yes. For the moderate
  cases the escalating warning injected at repeats 3–7 gives a
  concrete "try something fundamentally different" signal the model
  currently never receives, creating a genuine recovery path before
  termination. For the extreme 26–29× verbatim loops, raise-at-8
  reclaims ~20 wasted steps and large wall-clock/cost without changing
  the already-failing outcome (net budget/cost win, protects the round
  from one task monopolising wall-clock). The assigned task's specific
  interpreter-shadowing fix is a model capability gap; this candidate
  does not claim to flip it, but it stops the loop that made the whole
  cluster hit budget_exceeded and gives the model its only
  in-context chance to change course.
- expected_global_gain: 12/50 R0 tasks (~24%) are
  `budget_exceeded`-at-80-steps, dominated by verbatim command loops.
  Even a modest recovery rate on the moderate cases flips tasks;
  cluster is large and the mechanism is uniform, so this generalises
  across domains (kill-loops, install-loops, inspect-loops).
- regression_risk: LOW. `warn_threshold=3` only fires on the 3rd
  *consecutive identical* call — legitimate exploration varies inputs
  and never trips Strategy 1. `threshold=8` raises only on 8 verbatim
  repeats, a pattern no passing R0 task exhibits (passing tasks:
  10–37 steps, no high-repeat histograms). Strategy 2 (name-only) is
  neutralised via `name_warn_threshold=999` so the single-tool (Bash)
  setup never false-warns. `LoopDetectedError` → `exit_reason=loop_detected`
  (handled cleanly in runloop.py:781, not `error`), so it cannot trip
  the replay crash gate.
- cost_shift: NET DOWN. Terminating 26–29× loops at repeat 8 removes
  ~18–21 wasted 80-step-budget iterations per affected task; the only
  additions are a few short warning strings appended to tool results.
