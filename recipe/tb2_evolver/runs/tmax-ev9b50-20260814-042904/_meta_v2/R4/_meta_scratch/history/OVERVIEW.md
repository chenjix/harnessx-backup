# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `426fa407196e6210` | 30/50 | 30 | accept | yes |
| R1 | `5e10927aff416016` | 29/50 | 29, 28, 30 | accept | no |
| R2 | `5e10927aff416016` | 28/50 | 29, 28, 30 | accept | no |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).