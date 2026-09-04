# Journal Context for R7

Aggregated from `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep10-i2/learnings.md` across 5 prior round(s); recent window shows the last 5.

## Lever scoreboard (all-time)

Raw prediction hits = ``flipped / (attributed_predicted + side_effects)`` — a round that flipped 3 tasks but broke 5 others registers as 3/8, not 100%. The **Posterior** column smooths this with a Beta(1+wh, 1+wm) prior where wh/wm are time-decayed hits/misses (0.9^rounds_ago): low-n levers get pulled toward 0.5, recent rounds weigh more than ancient ones. ``n_eff`` is the effective weighted sample size — when it's small the posterior is uncertain even if the raw ratio looks extreme.

| Lever | Attempts | Accepted | Reverted | Raw hits | Posterior (n_eff) | Side-effects |
|-------|---------:|---------:|---------:|---------:|:------------------|-------------:|
| action | 0 | 0 | 0 | — | — | — |
| configuration | 0 | 0 | 0 | — | — | — |
| control | 3 | 2 | 1 | — | — | — |
| instruction | 2 | 2 | 0 | — | — | — |

## Recent hypotheses (last 5)

| Round | Label | Levers | Outcome | Predicted | Attribution |
|------:|-------|--------|:-------:|:---------:|-------------|
| R1 | functional verify gate | instruction,control | accepted | 6 | pending |
| R2 | warn-only loop breaker | control | accepted | 9 | pending |
| R3 | decompose + loop-escape strategy | instruction | accepted | 4 | pending |
| R4 | windowed no-progress loop breaker | control | reverted | 3 | pending |
| R6 | no-op: remaining failures are capabilit… | ? | accepted | 0 | pending |

## Reverted hypotheses — do not re-propose without new evidence (1)

- R4 `h_windowed_repeat_break_v1`: windowed no-progress loop breaker

## Full journal

For hypothesis bodies, evidence citations, and uncertainty notes, read `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep10-i2/learnings.md` directly. This context file is an index, not a replacement.
