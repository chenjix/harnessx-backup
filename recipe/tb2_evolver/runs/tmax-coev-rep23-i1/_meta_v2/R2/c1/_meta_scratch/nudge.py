import json
m=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep23-i1-r0-fe-c4-traj/task_000015_89886d8d.messages.json'))
for i,e in enumerate(m):
    c=str(e.get('content',''))
    low=c.lower()
    if 'repeat' in low or ('same' in low and 'command' in low) or 'different approach' in low or 'breaker' in low:
        print('==',i,e.get('role'))
        print(c[:350])
        print()
