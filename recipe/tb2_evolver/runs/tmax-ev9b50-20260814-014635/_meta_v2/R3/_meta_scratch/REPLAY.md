# Replay gate passed

Config under test: `/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-ev9b50-20260814-014635/_meta_v2/R3/config.yaml`

## ✓ `__synthetic_smoke__`

- kind: `ok_synthetic_smoke`
- exit_reason: `done`
- steps: 2
- tokens: 6178
- cost: $0.020
- elapsed: 4.6s

Replay uses the actual run loop as oracle — any crash, 400, assertion, or `exit_reason=error` fails the gate. Fix the failing component (tool return shape / processor hook / template reference) and re-verify.