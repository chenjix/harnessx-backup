# Replay gate passed

Config under test: `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep22-i2/_meta_v2/R1/c2/config.yaml`

## ✓ `__synthetic_smoke__`

- kind: `ok_synthetic_smoke`
- exit_reason: `done`
- steps: 2
- tokens: 3017
- cost: $0.011
- elapsed: 5.0s

Replay uses the actual run loop as oracle — any crash, 400, assertion, or `exit_reason=error` fails the gate. Fix the failing component (tool return shape / processor hook / template reference) and re-verify.