# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `b93b31cba81de7cb` | 11/50 | 11 | accept | yes |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).