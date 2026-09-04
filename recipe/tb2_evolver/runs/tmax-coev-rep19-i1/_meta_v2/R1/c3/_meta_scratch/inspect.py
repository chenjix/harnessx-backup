import json
d=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep19-i1-r0-traj/task_000118_3043e92d.messages.json'))
print('num messages', len(d))
for i,m in enumerate(d):
    role=m.get('role')
    c=m.get('content')
    if isinstance(c,list):
        c=' '.join(str(x.get('text',x)) for x in c if isinstance(x,dict))
    c=(c or '')
    tc=m.get('tool_calls')
    print('==',i, role, '==')
    print(c[:500])
    if tc:
        for t in tc:
            print('   TOOL', str(t)[:400])
