# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `56db078eb6e975ee` | 29/50 | 29 | accept | yes |
| R1 | `25035bcc34902e8e` | 30/50 | 30, 32, 34 | accept | no |
| R2 | `25035bcc34902e8e` | 32/50 | 30, 32, 34 | accept | no |

Incumbent: R2 (gate compares against the MEAN of its repeats, not its best single draw).