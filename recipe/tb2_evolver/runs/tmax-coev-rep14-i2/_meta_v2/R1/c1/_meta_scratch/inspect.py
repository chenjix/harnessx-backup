import json
data=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i2-r0-traj/task_000015_89886d8d.messages.json'))
print(type(data), len(data) if isinstance(data,list) else '')
if isinstance(data,list):
    for i,m in enumerate(data):
        r=m.get('role','?')
        c=m.get('content','')
        if isinstance(c,list):
            c=' '.join(str(x.get('text',x))[:400] for x in c)
        print("="*30, i, r)
        print(str(c)[:1500])
