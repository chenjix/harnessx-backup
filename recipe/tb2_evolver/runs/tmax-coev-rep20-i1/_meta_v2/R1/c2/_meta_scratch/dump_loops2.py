import json
from collections import Counter
for tid in ['task_000344_e265c898','task_000010_644ab1c2']:
    f='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep20-i1-r0-traj/%s.messages.json'%tid
    d=json.load(open(f))
    full=[]
    for m in d:
        if m.get('role')=='assistant' and m.get('tool_calls'):
            for t in m['tool_calls']:
                fn=t.get('function',{})
                full.append(str(fn.get('arguments','')))
    c=Counter(full)
    print(tid, "distinct:", len(c), "total:", len(full), "top count:", c.most_common(1)[0][1])
    # consecutive identical max
    maxrun=1;cur=1
    for i in range(1,len(full)):
        if full[i]==full[i-1]: cur+=1;maxrun=max(maxrun,cur)
        else: cur=1
    print("  max consecutive identical:", maxrun)
