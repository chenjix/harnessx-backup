# Journal Context for R4

Aggregated from `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep14-i2/learnings.md` across 5 prior round(s); recent window shows the last 5.

## Lever scoreboard (all-time)

Raw prediction hits = ``flipped / (attributed_predicted + side_effects)`` — a round that flipped 3 tasks but broke 5 others registers as 3/8, not 100%. The **Posterior** column smooths this with a Beta(1+wh, 1+wm) prior where wh/wm are time-decayed hits/misses (0.9^rounds_ago): low-n levers get pulled toward 0.5, recent rounds weigh more than ancient ones. ``n_eff`` is the effective weighted sample size — when it's small the posterior is uncertain even if the raw ratio looks extreme.

| Lever | Attempts | Accepted | Reverted | Raw hits | Posterior (n_eff) | Side-effects |
|-------|---------:|---------:|---------:|---------:|:------------------|-------------:|
| action | 0 | 0 | 0 | — | — | — |
| configuration | 0 | 0 | 0 | — | — | — |
| control | 3 | 2 | 0 | — | — | — |
| instruction | 1 | 0 | 0 | — | — | — |

## Recent hypotheses (last 5)

| Round | Label | Levers | Outcome | Predicted | Attribution |
|------:|-------|--------|:-------:|:---------:|-------------|
| R1 | ensure requests for verifier | control | accepted | 5 | pending |
| R1 | verify-computed-results prompt | instruction | pending | 5 | pending |
| R2 | no-op: assigned focus is capability gap… | ? | reverted | 0 | pending |
| R2 | two-sided service lifecycle reminder | control | pending | 1 | pending |
| R3 | low-DPI OCR quality advisor | control | accepted | 2 | pending |

## Reverted hypotheses — do not re-propose without new evidence (1)

- R2 `h_noop_task_000264_sql_semantics`: no-op: assigned focus is capability gap, not harness

## Full journal

For hypothesis bodies, evidence citations, and uncertainty notes, read `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep14-i2/learnings.md` directly. This context file is an index, not a replacement.
