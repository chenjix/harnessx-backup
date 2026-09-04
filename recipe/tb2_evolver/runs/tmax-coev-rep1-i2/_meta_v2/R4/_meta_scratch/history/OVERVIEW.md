# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `fa2ba1c513ceab3d` | 30/50 | 30 | accept | yes |
| R1 | `b3d006743caece37` | 28/50 | 28, 29 | accept | no |
| R2 | `b3d006743caece37` | 29/50 | 28, 29 | accept | yes |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).