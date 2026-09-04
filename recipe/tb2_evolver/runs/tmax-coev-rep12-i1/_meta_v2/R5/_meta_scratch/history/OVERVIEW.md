# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `56db078eb6e975ee` | 29/50 | 29, 26 | accept | yes |
| R1 | `da67dbe1ed2f2c08` | 25/50 | 25 | reject | no |
| R2 | `56db078eb6e975ee` | 26/50 | 29, 26 | accept | yes |
| R3 | `461320acaacb4bcb` | 26/50 | 26 | accept | yes |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).