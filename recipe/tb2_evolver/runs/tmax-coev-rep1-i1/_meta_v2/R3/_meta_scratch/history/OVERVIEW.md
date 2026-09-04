# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `94c5f4a0d684456f` | 29/50 | 29 | accept | yes |
| R1 | `ea008f3efdea9f22` | 29/50 | 29 | accept | yes |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).