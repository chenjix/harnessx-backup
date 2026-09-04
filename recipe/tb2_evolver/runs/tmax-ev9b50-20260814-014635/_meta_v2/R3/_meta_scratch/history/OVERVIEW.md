# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `426fa407196e6210` | 29/50 | 29 | accept | yes |
| R1 | `41840bb080d7d22e` | 30/50 | 30 | accept | yes |

Incumbent: R1 (gate compares against the MEAN of its repeats, not its best single draw).