# Journal Context for R5

Aggregated from `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep14-i1/learnings.md` across 7 prior round(s); recent window shows the last 5.

## Lever scoreboard (all-time)

Raw prediction hits = ``flipped / (attributed_predicted + side_effects)`` — a round that flipped 3 tasks but broke 5 others registers as 3/8, not 100%. The **Posterior** column smooths this with a Beta(1+wh, 1+wm) prior where wh/wm are time-decayed hits/misses (0.9^rounds_ago): low-n levers get pulled toward 0.5, recent rounds weigh more than ancient ones. ``n_eff`` is the effective weighted sample size — when it's small the posterior is uncertain even if the raw ratio looks extreme.

| Lever | Attempts | Accepted | Reverted | Raw hits | Posterior (n_eff) | Side-effects |
|-------|---------:|---------:|---------:|---------:|:------------------|-------------:|
| action | 0 | 0 | 0 | — | — | — |
| configuration | 0 | 0 | 0 | — | — | — |
| control | 5 | 0 | 1 | — | — | — |
| instruction | 2 | 0 | 1 | — | — | — |

## Recent hypotheses (last 5)

| Round | Label | Levers | Outcome | Predicted | Attribution |
|------:|-------|--------|:-------:|:---------:|-------------|
| R3 | lossy-source extraction + real-input va… | instruction | reverted | 3 | pending |
| R3 | break degenerate low-information tool-c… | control | pending | 4 | pending |
| R3 | provision HTTP verifier dependency (req… | control | pending | 2 | pending |
| R4 | computed-result plausibility + full-ran… | instruction | pending | 3 | pending |
| R4 | polarity-neutral lifecycle self-verify | control | pending | 1 | pending |

## Reverted hypotheses — do not re-propose without new evidence (2)

- R1 `h_step_budget_reminder_v1`: step-budget deliverable reminder
- R3 `h_ocr_extraction_discipline_v1`: lossy-source extraction + real-input validation discipline

## Full journal

For hypothesis bodies, evidence citations, and uncertainty notes, read `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep14-i1/learnings.md` directly. This context file is an index, not a replacement.
