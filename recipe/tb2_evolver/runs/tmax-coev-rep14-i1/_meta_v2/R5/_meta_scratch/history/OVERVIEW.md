# Round-by-round history

Scores are pass counts on the same task set, measured once per round.
The same config measured twice can differ by several tasks — treat a
difference smaller than the spread of `repeats` as noise, not signal.

| Round | Config | Score | Repeats of this config | Gate | Changed |
|------:|--------|------:|-----------------------|------|---------|
| R0 | `56db078eb6e975ee` | 33/50 | 33, 29, 30 | accept | yes |
| R1 | `79b13b4bb63f7042` | 29/50 | 29 | reject | no |
| R2 | `56db078eb6e975ee` | 29/50 | 33, 29, 30 | accept | yes |
| R3 | `d12cd8f944c4a405` | 27/50 | 27 | reject | no |

Incumbent: R0 (gate compares against the MEAN of its repeats, not its best single draw).