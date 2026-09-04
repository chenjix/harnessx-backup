# Replay gate passed

Config under test: `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep23-i1/_meta_v2/R1/c1/config.yaml`

## ✓ `__synthetic_smoke__`

- kind: `ok_synthetic_smoke`
- exit_reason: `done`
- steps: 2
- tokens: 2961
- cost: $0.010
- elapsed: 4.1s

Replay uses the actual run loop as oracle — any crash, 400, assertion, or `exit_reason=error` fails the gate. Fix the failing component (tool return shape / processor hook / template reference) and re-verify.