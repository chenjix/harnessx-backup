# Journal Context for R3

Aggregated from `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep15-i1/learnings.md` across 5 prior round(s); recent window shows the last 5.

## Lever scoreboard (all-time)

Raw prediction hits = ``flipped / (attributed_predicted + side_effects)`` — a round that flipped 3 tasks but broke 5 others registers as 3/8, not 100%. The **Posterior** column smooths this with a Beta(1+wh, 1+wm) prior where wh/wm are time-decayed hits/misses (0.9^rounds_ago): low-n levers get pulled toward 0.5, recent rounds weigh more than ancient ones. ``n_eff`` is the effective weighted sample size — when it's small the posterior is uncertain even if the raw ratio looks extreme.

| Lever | Attempts | Accepted | Reverted | Raw hits | Posterior (n_eff) | Side-effects |
|-------|---------:|---------:|---------:|---------:|:------------------|-------------:|
| action | 0 | 0 | 0 | — | — | — |
| configuration | 0 | 0 | 0 | — | — | — |
| control | 5 | 1 | 0 | — | — | — |
| instruction | 1 | 1 | 0 | — | — | — |

## Recent hypotheses (last 5)

| Round | Label | Levers | Outcome | Predicted | Attribution |
|------:|-------|--------|:-------:|:---------:|-------------|
| R1 | break truncation spiral (force-act) | control,instruction | accepted | 5 | pending |
| R1 | rigorous completion guard (c7) | control | pending | 2 | pending |
| R2 | final-state process/resource hygiene | control | pending | 2 | pending |
| R2 | correctness self-verify (value audit) | control | pending | 5 | pending |
| R2 | graded-artifact clean re-run guard (c2) | control | pending | 2 | pending |

## Full journal

For hypothesis bodies, evidence citations, and uncertainty notes, read `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep15-i1/learnings.md` directly. This context file is an index, not a replacement.
