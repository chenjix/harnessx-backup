import json
d=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i2-r1-traj/task_000118_3043e92d/messages.json'))
for i,m in enumerate(d):
    r=m.get('role')
    c=m.get('content')
    if isinstance(c,list):
        c=' '.join(x.get('text','') for x in c if isinstance(x,dict) and x.get('type')=='text')
    tc=m.get('tool_calls')
    extra=''
    if tc:
        for t in tc:
            fn=t.get('function',{})
            extra += ' TOOLCALL:'+fn.get('name','')+' '+str(fn.get('arguments',''))[:800]
    print(f'--- [{i}] {r} ---')
    if c: print(str(c)[:600])
    if extra: print(extra[:900])
