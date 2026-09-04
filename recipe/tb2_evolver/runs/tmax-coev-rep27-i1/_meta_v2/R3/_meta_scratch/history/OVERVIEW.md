# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `56db078eb6e975ee` | 41/50 | 41 | accept | yes |
| R1 | `b873e3499c0b3538` | 43/50 | 43 | accept | yes |

Incumbent: R1 (gate compares against the MEAN of its repeats, not its best single draw).