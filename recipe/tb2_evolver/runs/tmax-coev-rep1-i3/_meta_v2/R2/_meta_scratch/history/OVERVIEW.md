# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `9618f5e0e4777dc1` | 27/50 | 27 | accept | yes |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).