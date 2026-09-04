# Candidates — Round 1 (rep19-i1)

Assigned focus: `task_000010_644ab1c2` (system_administration) fails with
`exit_reason=budget_exceeded` at the 80-step cap after 889s.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Add a content-based `RepetitionLoopBreaker` processor that detects when the
model repeats the same assistant turn (narration + tool-call signature) for N
consecutive turns — regardless of `finish_reason` or whether a tool call is
present — and hard-breaks it by pruning the poisoned duplicate turns from
context and injecting one decisive "take a different concrete step" redirect.

- Tasks affected: task_000010_644ab1c2, task_001321_658ce4a8 (same mechanism;
  also matches the shape of the other 80-step budget_exceeded tasks
  task_000118_3043e92d, task_000578_cebe85a5, task_001818_b251e5ea).
- Signal: `exit_reason=budget_exceeded` at exactly `steps=80` on 6 tasks; the
  message logs show the model emitting near-verbatim assistant turns dozens of
  times. Existing `LengthTruncationRecoveryProcessor` only fires on
  `finish_reason=length AND no tool_calls`; its nudge text never appears in any
  trajectory (`proc_nudge=0` across all budget tasks) — a soft nudge cannot
  outweigh the wall of the model's own repeated prior turns still in context.
- Verified (Read):
  - task_000010 steps [2,4,6,9,11,...,68]: assistant repeats
    "I've been stuck ... circular import ... only real solution is to rename
    the file. But the user specifically asked for /home/user/operator.py"
    ~15 tool-less turns; 16 passive "cut off by the token limit" continue
    nudges, each preceded by a no-tool-call assistant turn — the exact
    condition the length processor claims to catch, yet the loop persisted to
    budget exhaustion.
  - task_001321 steps [6..68]: 33 assistant turns, ALL carrying a
    **byte-identical** Bash command (measured: 1 distinct command across 33
    tool-call turns) plus near-identical "I've been stuck in a loop. Let me
    take a completely different approach" narration. The prior-rep loop breaker
    resets on any tool call, so it would never catch this; the new fingerprint
    (content prefix + tool name/input) does.
- Why Control not Instruction: the model already *knows* it is looping (it says
  so every turn) — a prompt rule telling it "don't loop" adds nothing the model
  isn't already narrating. The decisive fix is *mechanical*: remove the poisoned
  duplicate history from the context window so the runaway prose stops
  re-priming the next completion, and force a single redirect. Only an
  `on_before_model` hook can mutate the assembled context; the prompt cannot.
- Why Control not Configuration (tuning the existing length processor): that
  processor's trigger condition (`finish_reason=length AND no tool_calls`) is
  structurally wrong for this cluster — task_001321's loop carries a tool call
  every turn and never hits the length condition. No knob on the existing
  processor can widen it to content-based, tool-call-agnostic detection.
- Retroactive check (A-corrective): partial-yes. For task_000010: pruning the
  ~15 duplicate "rename the file" turns and injecting "abandon this line, take a
  DIFFERENT concrete step" frees the model from its self-reinforcing dead end
  and reclaims ~70 wasted steps for fresh attempts (e.g. run python from a
  different cwd / set PYTHONPATH) instead of 15 identical loops to budget
  exhaustion. For task_001321: the same command failing 33 times is exactly the
  redirect's target ("if a command kept failing the same way, change the
  command"). The breaker does not *inject* the solution (that is a model
  capability), but it converts a guaranteed 0-progress budget burn into many
  fresh attempts — the necessary precondition for any recovery.
- expected_global_gain: 6 tasks currently die at the 80-step budget cap in
  degenerate self-repetition loops (all domains, notably 0/5 system_admin).
  Breaking the loop gives each a real chance to converge; even partial recovery
  on 2 flips the assigned task's cluster.
- regression_risk: Low-moderate. The breaker only fires after 3 *consecutive*
  near-identical turns (content prefix + identical tool signature), a shape
  healthy runs never produce (normal retries differ turn-to-turn). Pruning is
  bounded to messages matching the loop fingerprint plus paired tool results
  and passive nudges; the first message (task anchor) is always preserved and
  the redirect keeps the context ending on a valid user turn. Passing tasks
  (short step counts, varied turns) never trip it.
- cost_shift: Net negative (cheaper). Loops that currently run to 80 steps /
  hundreds of seconds get cut short; pruning also shrinks the context sent to
  the model on the breaking turn. No added cost on non-looping tasks (hook is a
  cheap fingerprint compare per turn).
