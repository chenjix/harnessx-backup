import json
d=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep20-i1-r0-traj/task_000024_a0664029.messages.json'))
for i in [2,3,4,5,54,55,56]:
    m=d[i]
    print("==== [%d] role=%s"%(i,m.get('role')))
    print("content:", repr(m.get('content'))[:600])
    print("tool_calls:", repr(m.get('tool_calls'))[:400])
    print("keys:", list(m.keys()))
