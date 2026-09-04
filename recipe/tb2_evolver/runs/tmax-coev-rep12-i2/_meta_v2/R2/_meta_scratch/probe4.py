import json
h='/fsx/home/jixuan.chen/harnessx-backup/recipe/tb2_evolver/runs/tmax-coev-rep12-i2/_meta_v2/R2/_meta_scratch/history/'
r0=json.load(open(h+'R0_per_task.json'))
r1=json.load(open(h+'R1_per_task.json'))
gained=[k for k in r0 if not r0[k] and r1.get(k)]
lost=[k for k in r0 if r0[k] and not r1.get(k)]
print('R0 pass:',sum(r0.values()),'R1 pass:',sum(r1.values()))
print('GAINED (0->1):',gained)
print('LOST (1->0):',lost)
