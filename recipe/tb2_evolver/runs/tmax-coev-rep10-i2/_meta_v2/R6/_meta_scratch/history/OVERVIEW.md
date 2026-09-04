# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `25b21f3df01b6efe` | 21/50 | 21 | accept | yes |
| R1 | `2475505de48456b9` | 23/50 | 23 | accept | yes |
| R2 | `c05cf7138fe65144` | 23/50 | 23 | accept | yes |
| R3 | `0140f0bb689a77f5` | 26/50 | 26, 21, 21 | accept | yes |
| R4 | `0d3a6ab8a3b656f0` | 22/50 | 22 | reject | no |
| R5 | `0140f0bb689a77f5` | 21/50 | 26, 21, 21 | reject | no |

Incumbent: R3 (gate compares against the MEAN of its repeats, not its best single draw).