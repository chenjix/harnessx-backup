# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `fa2ba1c513ceab3d` | 30/50 | 30 | accept | yes |
| R1 | `b3d006743caece37` | 28/50 | 28, 29 | accept | no |
| R2 | `b3d006743caece37` | 29/50 | 28, 29 | accept | yes |
| R3 | `77c4232afc122ee5` | 32/50 | 32, 34, 34, 32 | accept | yes |
| R4 | `948a9af1b4ccd988` | 30/50 | 30 | accept | yes |
| R5 | `77c4232afc122ee5` | 34/50 | 32, 34, 34, 32 | accept | no |
| R5 | `77c4232afc122ee5` | 34/50 | 32, 34, 34, 32 | accept | no |
| R6 | `77c4232afc122ee5` | 32/50 | 32, 34, 34, 32 | accept | yes |
| R7 | `7876903850516884` | 35/50 | 35, 35 | accept | no |

Incumbent: R7 (gate compares against the MEAN of its repeats, not its best single draw).