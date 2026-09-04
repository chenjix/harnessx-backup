# Journal Context for R9

Aggregated from `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep12-i4/learnings.md` across 8 prior round(s); recent window shows the last 5.

## Lever scoreboard (all-time)

Raw prediction hits = ``flipped / (attributed_predicted + side_effects)`` — a round that flipped 3 tasks but broke 5 others registers as 3/8, not 100%. The **Posterior** column smooths this with a Beta(1+wh, 1+wm) prior where wh/wm are time-decayed hits/misses (0.9^rounds_ago): low-n levers get pulled toward 0.5, recent rounds weigh more than ancient ones. ``n_eff`` is the effective weighted sample size — when it's small the posterior is uncertain even if the raw ratio looks extreme.

| Lever | Attempts | Accepted | Reverted | Raw hits | Posterior (n_eff) | Side-effects |
|-------|---------:|---------:|---------:|---------:|:------------------|-------------:|
| action | 0 | 0 | 0 | — | — | — |
| configuration | 0 | 0 | 0 | — | — | — |
| control | 4 | 4 | 0 | — | — | — |
| instruction | 1 | 1 | 0 | — | — | — |

## Recent hypotheses (last 5)

| Round | Label | Levers | Outcome | Predicted | Attribution |
|------:|-------|--------|:-------:|:---------:|-------------|
| R4 | hard-terminate degenerate assistant-tur… | control | accepted | 10 | pending |
| R5 | fuzzy near-identical turn terminator | control | accepted | 5 | pending |
| R6 | no-op: loop lever saturated, remainder … | ? | accepted | 0 | pending |
| R7 | no-op: verify-workflow fires yet capabi… | ? | accepted | 0 | pending |
| R8 | no-op: fresh full sweep confirms capabi… | ? | accepted | 0 | pending |

## Full journal

For hypothesis bodies, evidence citations, and uncertainty notes, read `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep12-i4/learnings.md` directly. This context file is an index, not a replacement.
