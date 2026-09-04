# Candidates — R3 / c1

Assigned focus: `task_000015_89886d8d` (software_engineering, reward=0,
exit_reason=done, 28 steps). Deliverable `migrate.py` scored accuracy
0.6678 vs 0.98 required on a hidden 2000-URL dataset.

## Candidate C-001
[lens: capability-gap | lever: instruction | intent: corrective]

Add general lossy-source-extraction + validate-against-real-inputs discipline
to the system prompt (sidecar `system_prompt.txt` read by
`SiblingSystemPromptBuilder`): when OCR/scan output is garbled, exhaust standard
remedies (upscale image / fix DPI, re-binarize, try multiple PSM modes,
cross-check attempts) BEFORE inferring a schema from a couple of samples; and
validate a built script/parser end-to-end against the REAL task inputs, not
self-fabricated inputs that echo the agent's own assumptions.

- Tasks affected (corrective, same mechanism across distinct inputs):
  - `task_000015_89886d8d` — OCR routing-schema image → migrate.py
  - `task_000505_50b5162d` — OCR SSH key from evidence.png → detector script
  - `task_000536_9c16e8ef` — OCR employee-id from memo.png → SQLite audit script
- Signal: all three `tesseract` tasks fail (`reward=0`); the two `tesseract`
  tasks that PASS (`task_000338_27d6a1be`, `task_001652_86e1d185`) either OCR a
  simpler artifact or iterate with alternate tools. Shared shape: run
  `tesseract <img> stdout` once/twice on the raw low-res image, get garbled
  output, then trust/guess it and build the whole deliverable on top.
- Verified (Read, r2-traj bodies):
  - task_000015 msg 22: OCR emits `Warning: Invalid resolution 0 dpi. Using 70
    instead` and garbage (`eatalogyitem`, `Wept=<depariment>`, `esss_id`); msgs
    26–288 the agent fiddles contrast/threshold/PSM (never upscales/fixes DPI),
    hits the output-token limit in a repetition loop (msg 288 truncation notice),
    then msg 296 "proceed with the mapping I can infer from the sample URLs",
    hardcodes a guessed 3-route schema; msg 330 "verifies" only against the 4
    self-consistent sample URLs; result.json accuracy 0.6678.
  - task_000505 msg (tesseract stdout): garbled ed25519 key, hardcoded and
    "tested" against a self-echoed string → verifier `2 of 2 evil bypassed`.
  - task_000536: single `tesseract /app/target_memo.png stdout`, extracted id
    fed straight into `run_audit.sh`; final_pytest is a CSV quoting/format miss
    checkable against the real DB.
- Why Instruction not Action/Control: the capability is present — tesseract,
  PIL, and Bash all work; the passing tasks 338/1652 prove clean extraction is
  achievable with the same toolset. The gap is *strategy/knowledge* of WHEN to
  upscale/re-run and WHAT to validate against, i.e. the agent does not know to
  apply standard OCR remedies or to test against real inputs. A new tool would
  duplicate tesseract; a Control hook cannot supply the domain-agnostic
  reasoning "your first OCR is garbage, fix the image and re-read." The R0
  running prompt is the minimal 5-line default (confirmed:
  `SiblingSystemPromptBuilder` falls back to `DEFAULT_TMAX_PROMPT` when no
  sidecar exists, and r2-traj bodies show no extraction/verify discipline), so
  this is net-new instruction, not a duplicate of any live guidance.
- Retroactive check (A-corrective): yes — if the agent had upscaled the 800×400
  image (the "0 dpi" fix) and re-run, tesseract would have produced a clean
  read of the schema instead of a guessed 3-route mapping, and the
  validate-against-real-inputs clause would have pushed it to probe
  URL-encoding / extra-param edge cases the 4 samples never covered — the exact
  gap between 0.6678 and 0.98. For 505 the format cross-check (ed25519 prefix)
  and real-binary test flips the false pass; for 536 the byte-format audit
  against the real DB output flips the quoting miss.

- expected_global_gain: Flips/relieves the OCR→script cluster (>=3 failing
  tasks: 015, 505, 536). Generalizes to any detector/transformer/parser task
  whose input is a lossy source or whose correctness lives in real-input edge
  cases — a recurring tmax shape.
- regression_risk: Low. Prompt-only, append-only guidance; removes no
  capability and changes no processor. Worst case: a few extra Bash
  verification/upscale calls on already-passing tasks; the two passing OCR tasks
  (338, 1652) already do clean extraction so the guidance only reinforces their
  habit. Non-OCR tasks see the "validate against real inputs" clause, which is
  generically safe.
- cost_shift: Small positive — a handful of extra image-preprocessing / real-
  input verification Bash calls on extraction tasks; likely net-neutral-to-
  favourable by converting silent wrong-answer finishes into corrected
  deliverables and by preventing the msg-288-style output-token repetition
  spiral (the agent gives up and loops precisely because it has no remedy to
  reach for).
- rollback_trigger: If the next round shows flat pass_rate AND
  task_000015/505/536 stay F on the same accuracy/format assertions, the
  residual is a pure OCR-model-capability limit (the model can't read even the
  upscaled image) — revert to the R0 minimal prompt. Also revert if any
  previously-passing OCR task (338, 1652) or short non-OCR task regresses to F.
