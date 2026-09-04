# Journal Context for R9

Aggregated from `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep12-i3/learnings.md` across 7 prior round(s); recent window shows the last 5.

## Lever scoreboard (all-time)

Raw prediction hits = ``flipped / (attributed_predicted + side_effects)`` — a round that flipped 3 tasks but broke 5 others registers as 3/8, not 100%. The **Posterior** column smooths this with a Beta(1+wh, 1+wm) prior where wh/wm are time-decayed hits/misses (0.9^rounds_ago): low-n levers get pulled toward 0.5, recent rounds weigh more than ancient ones. ``n_eff`` is the effective weighted sample size — when it's small the posterior is uncertain even if the raw ratio looks extreme.

| Lever | Attempts | Accepted | Reverted | Raw hits | Posterior (n_eff) | Side-effects |
|-------|---------:|---------:|---------:|---------:|:------------------|-------------:|
| action | 1 | 1 | 0 | — | — | — |
| configuration | 1 | 1 | 0 | — | — | — |
| control | 4 | 3 | 1 | — | — | — |
| instruction | 1 | 1 | 0 | — | — | — |

## Recent hypotheses (last 5)

| Round | Label | Levers | Outcome | Predicted | Attribution |
|------:|-------|--------|:-------:|:---------:|-------------|
| R3 | keep background services alive | instruction | accepted | 6 | pending |
| R4 | budget-aware verifier dep guard | action | accepted | 3 | pending |
| R5 | hard-block identical-command loops | control | reverted | 4 | pending |
| R7 | collapse max_tokens truncation loop | control | accepted | 9 | pending |
| R8 | durably break identical-command loops | control | accepted | 2 | pending |

## Reverted hypotheses — do not re-propose without new evidence (1)

- R5 `h_repeat_command_blocker_v1`: hard-block identical-command loops

## Full journal

For hypothesis bodies, evidence citations, and uncertainty notes, read `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep12-i3/learnings.md` directly. This context file is an index, not a replacement.
