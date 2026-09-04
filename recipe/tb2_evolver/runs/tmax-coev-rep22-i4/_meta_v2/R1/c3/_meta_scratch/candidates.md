# Candidates

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace `CustomSelfVerifyProcessor` with a subclass that injects a stronger
exit-time checklist forcing (a) independent re-derivation of at least one
output value by a different method and (b) a contradiction sweep between the
agent's own reasoning and its emitted output.

- Tasks affected (assigned focus + cluster):
  - task_000109_09ddd96b (data_science) — assigned focus
  - task_000328_80fb4c9f (data_science)
  - task_001653_c4cafa73 (data_science)
  - task_000505_50b5162d (security)
- Signal: all four are `finished=no_tool_calls` with a passing initial pytest
  and a failing final pytest whose assertions are *value* mismatches (wrong
  number / wrong string), not missing files or crashes. The agent produced a
  clean-running program with a semantically wrong answer and exited confident.
- Verified (Read, task_000109 messages.json):
  - step 46 tool output: program prints "Imputed samples: 2" and "Anomaly
    window ...: 1" — but the task's fully-imputed window should make an entire
    window zero.
  - step 48 embeddings.json: window index 4 = `[128, 0, 0]` (mean 128,
    variance 0).
  - step 49 assistant narration: *"Looking at the embeddings, window 4 has:
    Mean: 128 ... This suggests that window 4 has all zeros"* — an explicit
    self-contradiction (mean 128 is not "all zeros") that the agent never
    reconciled.
  - step 56/57: the stock self-verify checklist fired; the agent re-read the
    requirements and re-listed files (steps 58) but did NOT re-derive any value
    or resolve the 128-vs-zero contradiction, then exited (no_tool_calls).
  - Cross-cluster confirmation (result.json final_pytest tails):
    task_000328 final pytest `got 300` expected ~10 frames; task_001653
    `Centroid/Distance` numeric mismatch; task_000505 adversarial filter let
    "2 of 2 evil bypassed" — all confident wrong-value exits.
- Why Control not Instruction: the checklist is a mechanical, cross-task guard
  that must fire uniformly at the exact exit moment (on_after_model detecting a
  no-tool-call exit and injecting exactly one user message via the keepalive
  trick). The stock system prompt (SiblingSystemPromptBuilder) is read-only in
  spirit here and a static prompt rule cannot re-inject itself at the decisive
  exit turn the way this hook does; the existing self-verify hook already owns
  this slot, so the narrowest fix is to strengthen that hook's payload rather
  than add a parallel instruction.
- Why not just tune Configuration: `CustomSelfVerifyProcessor` exposes no knob
  for its message text — the checklist is a hardcoded module constant, so
  changing its content requires a Control-lever subclass, not a kwarg.
- Retroactive check (A-corrective): partial-yes. For task_000109 the agent had
  already surfaced the contradicting evidence in its own words; a checklist step
  that says "if a value contradicts something you concluded, that is a bug —
  fix it" and "re-derive one value by a different method" directly targets the
  decisive step and plausibly flips it. For the other three the re-derivation
  step gives a second chance to catch the wrong number. This is an
  attention/verification-discipline fix, not domain knowledge: it will not
  conjure a correct WAV parser out of nothing, but it makes the agent much more
  likely to notice the parser is wrong before committing.
- expected_global_gain: targets the single largest failure cluster on this
  benchmark — `no_tool_calls` confident exits on wrong computed values (30 of
  40 failures exit via `no_tool_calls`). Even a modest catch-rate on this
  cluster flips multiple tasks; generalizes to any computation task, seen or
  unseen.
- regression_risk: low. The change only alters the *text* of a one-shot message
  already injected on the exit turn; it adds no new fire conditions and keeps
  the fires-once contract. Worst case the agent spends a few extra Bash calls
  re-deriving a value on tasks that were already correct — the checklist ends
  with the same SUCCESS marker path, so a correct task still exits cleanly.
- cost_shift: small upward. Tasks that were exiting immediately may now run 1-3
  extra verification Bash calls. Bounded because the checklist fires at most
  once per task and the per-call output cap is unchanged. Net positive if it
  flips even one or two failures.
