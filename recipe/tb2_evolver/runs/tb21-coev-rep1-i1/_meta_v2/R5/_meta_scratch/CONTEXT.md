# Journal Context for R4

Aggregated from `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tb21-coev-rep1-i1/learnings.md` across 3 prior round(s); recent window shows the last 3.

## Lever scoreboard (all-time)

Raw prediction hits = ``flipped / (attributed_predicted + side_effects)`` — a round that flipped 3 tasks but broke 5 others registers as 3/8, not 100%. The **Posterior** column smooths this with a Beta(1+wh, 1+wm) prior where wh/wm are time-decayed hits/misses (0.9^rounds_ago): low-n levers get pulled toward 0.5, recent rounds weigh more than ancient ones. ``n_eff`` is the effective weighted sample size — when it's small the posterior is uncertain even if the raw ratio looks extreme.

| Lever | Attempts | Accepted | Reverted | Raw hits | Posterior (n_eff) | Side-effects |
|-------|---------:|---------:|---------:|---------:|:------------------|-------------:|
| action | 0 | 0 | 0 | — | — | — |
| configuration | 0 | 0 | 0 | — | — | — |
| control | 3 | 2 | 1 | — | — | — |
| instruction | 0 | 0 | 0 | — | — | — |

## Recent hypotheses (last 3)

| Round | Label | Levers | Outcome | Predicted | Attribution |
|------:|-------|--------|:-------:|:---------:|-------------|
| R1 | bound runaway context | control | accepted | 7 | pending |
| R2 | repeatable act-loop keepalive | control | accepted | 11 | pending |
| R3 | output-artifact-aware keepalive | control | reverted | 6 | pending |

## Reverted hypotheses — do not re-propose without new evidence (1)

- R3 `h_output_artifact_keepalive_v1`: output-artifact-aware keepalive

## Full journal

For hypothesis bodies, evidence citations, and uncertainty notes, read `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tb21-coev-rep1-i1/learnings.md` directly. This context file is an index, not a replacement.
