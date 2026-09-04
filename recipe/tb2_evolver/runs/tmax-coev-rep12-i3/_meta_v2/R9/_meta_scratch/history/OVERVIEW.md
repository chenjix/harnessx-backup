# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `b93b31cba81de7cb` | 11/50 | 11 | accept | yes |
| R1 | `7c5746d3fb6bec10` | 11/50 | 11 | accept | yes |
| R2 | `0ae6e469d153fb60` | 11/50 | 11 | accept | yes |
| R3 | `4b51c6de66e63ca0` | 14/50 | 14, 11 | accept | yes |
| R4 | `83a70136f245715b` | 13/50 | 13 | accept | yes |
| R5 | `fd6acac266a85a68` | 11/50 | 11, 11 | reject | no |
| R5 | `fd6acac266a85a68` | 11/50 | 11, 11 | reject | no |
| R6 | `4b51c6de66e63ca0` | 11/50 | 14, 11 | accept | yes |
| R7 | `0228cdac4c016747` | 13/50 | 13, 13 | accept | no |
| R7 | `0228cdac4c016747` | 13/50 | 13, 13 | accept | yes |

Incumbent: R7 (gate compares against the MEAN of its repeats, not its best single draw).