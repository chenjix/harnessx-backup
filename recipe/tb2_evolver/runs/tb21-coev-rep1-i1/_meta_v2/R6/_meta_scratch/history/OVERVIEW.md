# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `426fa407196e6210` | 3/18 | 3 | accept | yes |
| R1 | `7e9d17e3f0d325b8` | 3/18 | 3 | accept | yes |
| R2 | `79f0c8d3e9e18600` | 4/18 | 4, 3 | accept | yes |
| R3 | `44819f37227d2e4b` | 2/18 | 2 | reject | no |
| R4 | `79f0c8d3e9e18600` | 3/18 | 4, 3 | accept | yes |
| R5 | `9c93071ab3793068` | 3/18 | 3, 3 | accept | no |

Incumbent: R2 (gate compares against the MEAN of its repeats, not its best single draw).