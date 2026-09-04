import json
d=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep22-i4-r0-fe-c3-traj/task_000117_1b598e44.messages.json'))
msgs = d if isinstance(d,list) else d.get('messages',d)
print('num msgs', len(msgs))
for i,m in enumerate(msgs):
    r=m.get('role')
    c=m.get('content')
    if isinstance(c,list):
        c=' '.join(str(x.get('text',x)) if isinstance(x,dict) else str(x) for x in c)
    tc=m.get('tool_calls')
    print('===',i,r,'===')
    print((c or '')[:1400])
    if tc:
        for t in tc:
            fn=t.get('function',{})
            print('  TOOL:',fn.get('name'),str(fn.get('arguments'))[:600])
