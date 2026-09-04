import json,glob,os
d="/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep25-i1-r0-traj"
rows=[]
for f in sorted(glob.glob(d+"/*.result.json")):
    j=json.load(open(f))
    tid=j.get("task_id")
    ag=j.get("agent",{})
    rows.append((j.get("reward"),j.get("status"),ag.get("exit_reason"),ag.get("steps"),ag.get("finished"),j.get("domain"),tid))
rows.sort()
for r in rows:
    print(r)
print("TOTAL",len(rows),"reward1",sum(1 for r in rows if r[0]==1))
