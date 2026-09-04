# Candidates — R2 (proposal c6)

Assigned focus: `task_000264_ab8c7253` (data_querying) fails with `reward=0`,
`exit_reason=done`, `finished=no_tool_calls`, steps=17.

## Diagnosis

`task_000264` is NOT a budget/loop failure. The agent finished voluntarily,
fully confident, at 17 steps. Two output-contract errors:

1. **Off-by-one aggregate.** The recursive CTE base case
   `SELECT e.id AS manager_id, e.id AS subordinate_id` counts each employee as
   one of their own subordinates, so counts are inflated by 1 (Alice 12 vs
   expected 11). `final_pytest`: `At index 0 diff: 'Alice (CEO),12' != 'Alice (CEO),11'`.
2. **Query-plan string mismatch.** Test asserts literal `"USING INDEX" in
   content`; agent produced `USING COVERING INDEX` / `AUTOMATIC COVERING INDEX`,
   neither of which contains the substring `USING INDEX`.

The stock `CustomSelfVerifyProcessor` DID fire (msg 289 `_tb2_self_verify`
keepalive), but its checklist ("re-read task", "check files exist", "inspect
contents … confirm semantically correct") is too generic to surface a
computed off-by-one or an exact-string mismatch — the agent re-`ls`'d the
files, re-eyeballed the numbers it had already generated, and re-declared
success without ever recomputing a value or comparing the format literally.

This is a recurring shape in the round, not a one-off.

## Candidate C-001
[lens: failure | lever: control | intent: corrective]

Replace `CustomSelfVerifyProcessor` with `OutputContractVerifyProcessor`: same
one-shot exit-verify mechanism, but the injected checklist adds (3) a literal
byte-for-byte output-format audit and (4) an independent re-derivation of at
least one expected value by a different method.

- Tasks affected: `task_000264_ab8c7253`, `task_000536_9c16e8ef`
  (both `data_querying`, both `reward=0`, `exit_reason=done`,
  `finished=no_tool_calls`, ≤17 steps — voluntary exit on wrong output).
- Signal: `reward=0` + `exit_reason=done` + `finished=no_tool_calls`;
  `final_pytest` shows an *exact-match* assertion failure (value off-by-one in
  264, quoted-vs-bare CSV field in 536), not a missing file or crash.
- Verified (Read):
  - `task_000264` messages.json msg 88 — agent's CTE base case counts self
    (`e.id AS subordinate_id`); msg 222 output `Alice (CEO),12`; result.json
    `final_pytest`: `'Alice (CEO),12' != 'Alice (CEO),11'` and
    `'USING INDEX' not in ... 'USING COVERING INDEX ...'`. Msg 289 shows the
    self-verify tool fired; msgs 307–328 the agent merely re-`ls`'d and
    re-declared success without recomputing anything.
  - `task_000536` result.json `final_pytest`:
    `'EMP-4001,"Source Code Repo",...' != 'EMP-4001,Source Code Repo,...'`
    — values correct, quoting wrong; a literal format audit would have caught
    the extra quotes.
- Why Control not Instruction: the mechanism that must change is the *timing
  and force* of verification at the exact moment of voluntary exit — a hook
  already owns that decision point (`CustomSelfVerifyProcessor`). Editing the
  static system prompt cannot intercept the exit turn or guarantee the
  checklist is re-read at that moment; the existing control hook can and
  already does. This is a re-parameterisation of an existing control
  component, kept task-agnostic (no constants, paths, or IDs).
- Why not Configuration: the stock processor exposes no knob for its checklist
  text; changing behaviour requires a new class, so the minimal edit is a
  Control-lever swap.
- Retroactive check (A-corrective): yes — step 4 (independent re-derivation)
  forces a manual/alternate recount of subordinates, which surfaces the
  self-inclusion off-by-one in 264; step 3 (literal format audit) forces a
  char-by-char compare that surfaces the quoted-vs-bare CSV in 536. Both are
  exactly the class of error the generic checklist let through.
- expected_global_gain: relieves the "false-confidence voluntary exit on
  wrong output-contract" cluster. At least 2 confirmed data_querying tasks;
  plausibly helps the broader set of `done`/`no_tool_calls` failures that hinge
  on exact format/values (the round has ~8 such voluntary-exit failures).
- regression_risk: Low. The processor is behaviour-identical to the one it
  replaces except for a longer checklist string — same +1 user message, same
  keepalive, same one-shot firing, same singleton group / order. Worst case is
  a few extra Bash calls per task doing genuine re-verification (which is the
  intent). No previously-passing task loses a mechanism.
- cost_shift: Small positive on tokens — the checklist is ~2 sentences longer
  and may prompt 1–3 extra verification Bash calls on tasks that voluntarily
  exit. Net-neutral-to-favourable because it targets tasks that would
  otherwise fail; short tasks that exit correctly incur only the extra prompt
  text once.

## Not pursued (logged)

- `task_000264`'s two bugs are *also* partly model SQL-reasoning gaps. The
  harness fix does not inject SQL knowledge (that would violate the
  no-domain-knowledge rule); it strengthens the *verification discipline* so
  the model is prompted to re-derive and byte-compare — a general capability
  that helps it catch its own reasoning slips. If R2 shows the cluster
  unchanged, the residue is a pure capability gap and should be left to the
  model.
