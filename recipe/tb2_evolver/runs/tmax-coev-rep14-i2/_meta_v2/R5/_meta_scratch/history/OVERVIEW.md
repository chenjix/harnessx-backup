# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `56db078eb6e975ee` | 20/50 | 20, 17 | accept | yes |
| R1 | `388d2887f35b9967` | 19/50 | 19 | accept | yes |
| R2 | `8c44cc4f8d58fe05` | 17/50 | 17 | reject | no |
| R3 | `56db078eb6e975ee` | 17/50 | 20, 17 | accept | yes |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).