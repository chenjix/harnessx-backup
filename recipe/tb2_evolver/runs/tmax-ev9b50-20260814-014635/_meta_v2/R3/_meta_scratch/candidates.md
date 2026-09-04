# Candidates — R3

## Candidate C-003
[lens: failure | lever: instruction | intent: corrective]

Add a "Never Repeat — break out of stuck loops" section to the system-prompt
template: forbid issuing a byte-identical command twice in a row, cap same-sub-goal
retries at ~2 before forcing a strategy pivot, and prefer producing a best partial
deliverable over looping on one blocker until the step limit.

- Tasks affected: task_000015_89886d8d, task_000958_4bb2b05d, task_001701_95e3bbcb
- Signal: `agent.finished=max_steps`, `steps=80` on all three; measured
  max identical-consecutive-command run = 60 (015), 24 (958), 25 (1701).
  Every one of the 29 R2 *passing* tasks has max identical-consecutive-run = 1,
  so the loop shape uniquely separates this failing cluster from all passers.
- Verified (Read messages.json):
  - task_001701_95e3bbcb: last 25 assistant tool calls are the byte-identical
    command `grep -E "^[a-z_]+\(" /usr/include/json-c/json_object.h`; the agent
    started productively ("Let me break down this task...") then got stuck on the
    json-c API and repeated the same grep to the step limit.
  - task_000958_4bb2b05d: 24 consecutive identical
    `pkill -9 -f "./server" ... timeout 5 ./server 2>&1 & ...` calls to step 80.
  - task_000015_89886d8d: 60 consecutive identical
    `python3 -c "from PIL import Image ..."` calls, then thrashes, ends max_steps.
- Why Instruction not Control: the R1 hypothesis already tried the Control lever
  (`LoopDetectionProcessor`, window/warn/threshold knobs). R2 discovered the tmax
  eval path (`recipe/tmax_eval/agent_loop.run_agent`) does NOT run the harnessx
  processor pipeline — it consumes ONLY the resolved system prompt. The loop
  detector is therefore inert here; the only lever that reaches the agent on this
  benchmark is the system-prompt text. So the correct home for the loop-breaking
  intent is Instruction, not Control. (Confirmed by reading agent_loop.run_agent:
  the loop catches a chat exception → returns finished=error with no retry, and
  otherwise only appends assistant/tool messages; no processor hooks fire.)
- Retroactive check (A-corrective): yes — all three tasks were making no progress
  from repeated identical calls; a rule that (a) blocks the identical retry and
  (b) forces a strategy pivot after 2 same-failure attempts would have freed dozens
  of steps for the agent to try a different API/algorithm or ship a partial
  deliverable, converting an 80-step burn into either a pass or an earlier clean
  no_tool_calls exit.
- expected_global_gain: Closes the full 3-task max_steps loop cluster (data_querying,
  security, software_engineering — 3 distinct domains) by re-expressing R1's
  loop-detection intent on the ONLY lever that reaches the agent on this eval path.
  Generalizes to any future task where the model mechanically repeats a stuck call.
- regression_risk: Near-zero on pass-rate. All 29 R2 passing tasks have max
  identical-consecutive-run = 1 — legitimate work never repeats a command byte-for-
  byte, so the new rule never fires on a passer. Only added surface is one extra
  prompt section; the rest of the proven R2 template is preserved verbatim.
- cost_shift: Net negative (savings). The three targets each burned all 80 steps
  (015=282s, 958=188s, 1701=67s); breaking the loop earlier reclaims steps/tokens.
  No added cost on non-looping tasks (rule never triggers).
