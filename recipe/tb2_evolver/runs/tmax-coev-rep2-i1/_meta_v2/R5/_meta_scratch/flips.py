import json
h="/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep2-i1/_meta_v2/R5/_meta_scratch/history/"
R={r:json.load(open(h+r+"_per_task.json")) for r in ["R0","R1","R2","R3","R4"]}
tasks=list(R["R4"].keys())
print("Fragile (flipped at least once across R0-R4):")
for t in tasks:
    seq=[int(R[r][t]) for r in ["R0","R1","R2","R3","R4"]]
    if len(set(seq))>1:
        print(f"  {t}  R0-4={seq}")
print()
print("Always-fail (stuck, capability-bound):")
for t in tasks:
    seq=[int(R[r][t]) for r in ["R0","R1","R2","R3","R4"]]
    if sum(seq)==0:
        print(f"  {t}")
print()
print("Always-pass (stable):", sum(1 for t in tasks if all(R[r][t] for r in R)))
