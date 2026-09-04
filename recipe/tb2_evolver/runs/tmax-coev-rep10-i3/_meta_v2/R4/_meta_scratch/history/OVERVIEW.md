# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `f7f5b19bbd1bdcc9` | 15/50 | 15, 12 | accept | yes |
| R1 | `8de63cda9c7259f3` | 12/50 | 12 | reject | no |
| R2 | `f7f5b19bbd1bdcc9` | 12/50 | 15, 12 | accept | yes |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).