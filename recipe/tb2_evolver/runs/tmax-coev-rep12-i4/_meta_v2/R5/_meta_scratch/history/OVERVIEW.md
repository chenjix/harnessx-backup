# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `629c065b16f19385` | 10/50 | 10 | accept | yes |
| R1 | `c7f336b4ddb7f007` | 10/50 | 10 | accept | yes |
| R2 | `1934de0e782ed18a` | 10/50 | 10 | accept | yes |
| R3 | `de6d6a2d3a45717b` | 10/50 | 10 | accept | yes |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).