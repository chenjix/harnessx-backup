import json
m=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i1-r4-traj/task_000028_7fe033ac.messages.json'))
for i,msg in enumerate(m):
    r=msg.get('role')
    c=msg.get('content')
    if isinstance(c,list):
        c=' '.join(str(x.get('text',x)) for x in c if isinstance(x,dict)) if c else ''
    c=str(c).replace(chr(10),' ')
    tc=msg.get('tool_calls')
    extra=''
    if tc:
        for t in tc:
            fn=t.get('function',{})
            extra += ' CALL:'+str(fn.get('arguments',''))[:150].replace(chr(10),' ')
    print('--- %d [%s] %s%s' % (i, r, c[:180], extra[:200]))
