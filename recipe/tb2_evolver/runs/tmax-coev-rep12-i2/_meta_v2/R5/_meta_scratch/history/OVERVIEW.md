# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `9681acec7655601a` | 20/50 | 20 | accept | yes |
| R1 | `080f5b81bb402a87` | 20/50 | 20 | accept | yes |
| R2 | `e811c63a387829f6` | 21/50 | 21 | accept | yes |
| R3 | `cc27621187908a2d` | 22/50 | 22 | accept | yes |

Incumbent: R3 (gate compares against the MEAN of its repeats, not its best single draw).