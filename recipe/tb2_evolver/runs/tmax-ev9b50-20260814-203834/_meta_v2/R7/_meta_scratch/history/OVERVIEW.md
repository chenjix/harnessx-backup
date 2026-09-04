# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `94c5f4a0d684456f` | 30/50 | 30 | accept | yes |
| R1 | `8c47e4ca3b4c8ff9` | 31/50 | 31, 28 | accept | yes |
| R2 | `0b97559cbf40176a` | 30/50 | 30, 30, 27 | accept | no |
| R3 | `0b97559cbf40176a` | 27/50 | 30, 30, 27 | reject | no |
| R4 | `8c47e4ca3b4c8ff9` | 28/50 | 31, 28 | accept | yes |
| R5 | `ac657a6cf0f730f7` | 28/50 | 28 | accept | yes |

Incumbent: R1 (gate compares against the MEAN of its repeats, not its best single draw).