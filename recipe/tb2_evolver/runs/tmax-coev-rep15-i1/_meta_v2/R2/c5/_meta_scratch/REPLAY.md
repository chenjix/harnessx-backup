# Replay gate passed

Config under test: `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep15-i1/_meta_v2/R2/c5/config.yaml`

## ✓ `__synthetic_smoke__`

- kind: `ok_synthetic_smoke`
- exit_reason: `done`
- steps: 2
- tokens: 3003
- cost: $0.011
- elapsed: 4.8s

Replay uses the actual run loop as oracle — any crash, 400, assertion, or `exit_reason=error` fails the gate. Fix the failing component (tool return shape / processor hook / template reference) and re-verify.